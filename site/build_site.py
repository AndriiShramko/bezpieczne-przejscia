# -*- coding: utf-8 -*-
"""Static site generator — Bezpieczne Przejścia / SafeCross (PL root + /en/).

Every page carries the non-enforcement disclaimer banner (spec-legal §6).
No video, no lists of people/vehicles — aggregate charts only.
"""
import os
import shutil

BASE = "https://patrol.flyreelstudio.eu"
ROOT = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(ROOT, "public")

PAGES = ["index", "dashboard", "how-it-works", "resources", "compliance",
         "contact", "privacy"]

T = {
    "pl": {
        "lang": "pl", "prefix": "",
        "brand": "Bezpieczne Przejścia",
        "disclaimer": ("Demonstrator techniczny — NIE jest oficjalnym nadzorem ani "
                       "egzekwowaniem prawa, nie nakłada kar, nie identyfikuje osób, "
                       "nie prowadzi rejestru wykroczeń. Wszystkie prezentowane dane "
                       "są syntetyczne (demo)."),
        "nav": {"index": "Start", "dashboard": "Dashboard",
                "how-it-works": "Jak to działa", "resources": "Zasoby",
                "compliance": "Zgodność (RODO/AI Act)", "contact": "Kontakt",
                "privacy": "Prywatność"},
        "titles": {
            "index": "Bezpieczne Przejścia — anonimowy dashboard bezpieczeństwa przejść dla pieszych",
            "dashboard": "Dashboard bezpieczeństwa przejścia — dane zagregowane (demo)",
            "how-it-works": "Jak to działa — metoda i uczciwe ograniczenia",
            "resources": "Zasoby i koszty wdrożenia — dla samorządów i firm",
            "compliance": "Zgodność: RODO i AI Act — privacy by design",
            "contact": "Kontakt — Bezpieczne Przejścia",
            "privacy": "Polityka prywatności i nota prawna",
        },
    },
    "en": {
        "lang": "en", "prefix": "/en",
        "brand": "SafeCross — Bezpieczne Przejścia",
        "disclaimer": ("Technical demonstrator — NOT official surveillance or law "
                       "enforcement; it imposes no penalties, identifies no persons "
                       "and keeps no register of offences. All data shown is "
                       "synthetic (demo)."),
        "nav": {"index": "Home", "dashboard": "Dashboard",
                "how-it-works": "How it works", "resources": "Resources",
                "compliance": "Compliance (GDPR/AI Act)", "contact": "Contact",
                "privacy": "Privacy"},
        "titles": {
            "index": "SafeCross — anonymous pedestrian-crossing safety dashboard",
            "dashboard": "Crossing safety dashboard — aggregate data (demo)",
            "how-it-works": "How it works — method and honest limitations",
            "resources": "Resources & deployment costs — for governments and companies",
            "compliance": "Compliance: GDPR & AI Act — privacy by design",
            "contact": "Contact — SafeCross",
            "privacy": "Privacy policy & legal notice",
        },
    },
}

JSONLD_HOME = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Person","name":"Andrii Shramko","jobTitle":"Computer Vision / VR / 3D specialist",
  "knowsAbout":["computer vision","pedestrian safety analytics","3D Gaussian Splatting",
   "volumetric capture","vehicle speed estimation"],
  "sameAs":["https://www.linkedin.com/in/andriishramko"]},
 {"@type":"Organization","name":"Shramko Research Team","founder":"Andrii Shramko",
  "vatID":"PL7543116302"},
 {"@type":"WebSite","name":"Bezpieczne Przejścia / SafeCross","url":"%BASE%",
  "inLanguage":["pl","en"]}
]}
</script>"""

JSONLD_FAQ_PL = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
 {"@type":"Question","name":"Czy system identyfikuje ludzi lub pojazdy?",
  "acceptedAnswer":{"@type":"Answer","text":"Nie. System zlicza wyłącznie zagregowane zdarzenia (liczniki na minutę). Twarze i tablice są pikselizowane na etapie analizy, klatki nie są zapisywane, nie powstają żadne embeddingi twarzy ani rejestry."}},
 {"@type":"Question","name":"Czy to jest system egzekwowania prawa?",
  "acceptedAnswer":{"@type":"Answer","text":"Nie. To demonstrator analityki bezpieczeństwa. Egzekwowanie prawa pozostaje wyłączną kompetencją Policji i GITD z certyfikowanymi urządzeniami."}},
 {"@type":"Question","name":"Co dokładnie jest zapisywane na dysku?",
  "acceptedAnswer":{"@type":"Answer","text":"Wyłącznie liczniki zagregowane: statystyki na minutę (stats_bucket), realny czas obserwacji (coverage_bucket) i zdarzenia zdrowia kamer (camera_health). Żadnych klatek, żadnych danych osobowych."}},
 {"@type":"Question","name":"Skąd pewność, że luki w danych nie są zerami?",
  "acceptedAnswer":{"@type":"Answer","text":"Każdy przedział ma zapisany czas rzeczywistej obserwacji (observed_sec). Wskaźniki są normalizowane do czasu obserwacji, a przedziały bez pokrycia są prezentowane jako brak danych, nigdy jako zero."}}
]}
</script>"""

JSONLD_FAQ_EN = JSONLD_FAQ_PL  # EN page gets its own copy below

FORM_JS = """
<script>
(function(){
  var f=document.getElementById('leadform'); if(!f) return;
  f.addEventListener('submit', function(e){
    e.preventDefault();
    var btn=f.querySelector('button'); btn.disabled=true;
    var data=Object.fromEntries(new FormData(f).entries());
    fetch('/api/lead',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(data)})
      .then(function(r){ if(!r.ok) throw new Error(r.status);
        f.style.display='none';
        document.getElementById('formok').style.display='block';})
      .catch(function(){ btn.disabled=false;
        document.getElementById('formerr').style.display='block';});
  });
})();
</script>"""


def head(lang, page, extra=""):
    t = T[lang]
    other = "en" if lang == "pl" else "pl"
    fname = "" if page == "index" else page + ".html"
    return f"""<!DOCTYPE html>
<html lang="{t['lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t['titles'][page]}</title>
<meta name="description" content="{t['disclaimer'][:150]}">
<link rel="canonical" href="{BASE}{t['prefix']}/{fname}">
<link rel="alternate" hreflang="pl" href="{BASE}/{fname}">
<link rel="alternate" hreflang="en" href="{BASE}/en/{fname}">
<link rel="alternate" hreflang="x-default" href="{BASE}/{fname}">
<link rel="stylesheet" href="/assets/style.css">
{extra}
</head>
<body>
<div class="disclaimer" role="note">⚠️ {t['disclaimer']}</div>
<header>
 <div class="brand"><a href="{t['prefix']}/">🚸 {t['brand']}</a><span class="demo-badge">DEMO</span></div>
 <nav>{"".join(f'<a href="{t["prefix"]}/{p if p != "index" else ""}{".html" if p != "index" else ""}">{t["nav"][p]}</a>' for p in PAGES[:-1])}
 <a class="lang" href="{T[other]['prefix']}/{fname}">{'EN' if lang == 'pl' else 'PL'}</a></nav>
</header>
<main>"""


def foot(lang):
    t = T[lang]
    imprint = ("Administrator danych (demo): Andrii Shramko, JDG, NIP PL7543116302 · "
               if lang == "pl" else
               "Data controller (demo): Andrii Shramko, sole trader (PL), VAT PL7543116302 · ")
    return f"""</main>
<footer>
 <p>⚠️ {t['disclaimer']}</p>
 <p>{imprint}<a href="{t['prefix']}/privacy.html">{t['nav']['privacy']}</a> ·
 <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a> ·
 <a href="https://www.linkedin.com/in/andriishramko" rel="me">LinkedIn</a></p>
 <p class="fine">Bezpieczne Przejścia / SafeCross — privacy-first road-safety analytics demonstrator · Andrii Shramko / Shramko Research Team</p>
</footer>
</body></html>"""


def page_index(lang):
    if lang == "pl":
        body = """
<section class="hero">
 <h1>Anonimowy dashboard bezpieczeństwa przejść dla pieszych</h1>
 <p class="lead">Kamera + wizja komputerowa → <strong>wyłącznie zagregowane liczniki
 zachowań</strong>: piesi/godz., % pieszych z głową w dół (proxy uwagi),
 kierowcy nieustępujący pierwszeństwa, konflikty. Bez identyfikacji osób,
 bez rejestru, bez kar. Twarze i tablice pikselizowane, klatki nie są zapisywane.</p>
 <p><a class="cta" href="/dashboard.html">Zobacz dashboard (dane syntetyczne)</a>
    <a class="cta ghost" href="/contact.html">Dla samorządu / Dla firm</a></p>
</section>
<section class="cols3">
 <div><h3>🔒 Prywatność jako produkt</h3><p>Na dysk trafiają wyłącznie liczniki
 zagregowane. Tożsamości nie są utrwalane, rejestr nie istnieje. Pikselizacja twarzy
 i tablic na etapie analizy; brak embeddingów twarzy; efemeryczne ID trackingu tylko w RAM.</p></div>
 <div><h3>📊 Metryki, którym można ufać</h3><p>Każdy wskaźnik jest normalizowany do
 rzeczywistego czasu obserwacji (coverage). Luki w transmisji to «brak danych»,
 nigdy fałszywe zera. Failover kamer widoczny wprost na dashboardzie.</p></div>
 <div><h3>🛠 Ekspertyza, nie pudełko</h3><p>Demonstrator sprzedaje kompetencję:
 wdrożenia analityki bezpieczeństwa ruchu, doradztwo CV/AI, integracje.
 Autor: Andrii Shramko — specjalista computer vision / VR / 3D (Polska).</p></div>
</section>
<section>
 <h2>Dla kogo</h2>
 <ul>
  <li><strong>Samorządy i zarządy dróg</strong> — obiektywne dane przed/po zmianie
  infrastruktury (azyl, doświetlenie, wyniesienie przejścia).</li>
  <li><strong>Integratorzy i firmy smart-city</strong> — warstwa analityki
  behawioralnej jako podwykonawstwo.</li>
  <li><strong>Projekty badawcze i granty UE</strong> — mierzalne wskaźniki
  bezpieczeństwa pieszych.</li>
 </ul>
</section>
<section>
 <h2>Co już umiemy</h2>
 <ul>
  <li>Zliczać pieszych i pojazdy na godzinę (bez kalibracji kamery).</li>
  <li>Mierzyć zajętość przejścia i wykrywać <strong>nieustąpienie pierwszeństwa</strong>
  pieszemu (zdarzenie topologiczne — pojazd przejeżdża przez zebrę bez zatrzymania,
  gdy pieszy na niej jest).</li>
  <li>Szacować <strong>% pieszych z głową w dół</strong> (próbkowany proxy uwagi,
  z przedziałem ufności — nie oskarżenie konkretnej osoby).</li>
  <li>Pokazywać trendy godzina×dzień, pokrycie obserwacją i status failover kamer.</li>
  <li>Robić to prywatnie: pikselizacja twarzy/tablic, zero klatek na dysku, zero rejestru.</li>
 </ul>
 <h2>Co możemy dodać</h2>
 <ul>
  <li><strong>Czasy konfliktu w sekundach (PET / TTC)</strong> i near-miss — na kamerze
  skalibrowanej metrycznie: liczbowy wskaźnik „o włos od wypadku”, zanim dojdzie do kolizji.</li>
  <li><strong>Raport przed/po</strong> dla konkretnej interwencji (azyl, wyniesienie,
  doświetlenie, sygnalizacja) — twardy dowód, czy zmiana zadziałała.</li>
  <li><strong>Analiza pory nocnej i pogody</strong>, ranking najniebezpieczniejszych
  przejść w mieście, alerty progowe.</li>
  <li><strong>Pomiar prędkości z kamery</strong> jako warstwa przesiewowa (screening)
  wskazująca kandydatów dla certyfikowanego urządzenia organu — nigdy jako dowód.</li>
  <li>Integracja z istniejącymi kamerami miejskimi i panelami samorządu.</li>
 </ul>
 <h2>Jak zmieniamy bezpieczeństwo pieszych</h2>
 <p>Łańcuch przyczynowy jest prosty: <strong>mierzymy → wskazujemy najbardziej
 niebezpieczne przejścia → rekomendujemy interwencję → mierzymy jej efekt.</strong>
 Dziś decyzje o azylu, wyniesieniu czy doświetleniu przejścia zapadają często „na
 wyczucie” i bez sprawdzenia skutku. My dostarczamy ciągły, obiektywny, anonimowy
 sygnał: gdzie kierowcy nie ustępują, gdzie piesi są nieuważni, o których godzinach
 rośnie ryzyko — a po zmianie infrastruktury pokazujemy w liczbach, czy realnie
 spadło. To zamienia bezpieczeństwo pieszych z domysłu w mierzalny, powtarzalny proces.</p>
</section>
<section id="kontakt" class="card">
 <h2>Porozmawiajmy o Twoim przejściu</h2>
 <p>Napisz — dla samorządu, firmy czy projektu badawczego. Odpowiadam w 1–2 dni robocze.</p>
 """ + lead_form_html("pl") + """
</section>"""
    else:
        body = """
<section class="hero">
 <h1>Anonymous pedestrian-crossing safety dashboard</h1>
 <p class="lead">Camera + computer vision → <strong>aggregate behaviour counters
 only</strong>: pedestrians/hour, % head-down (attention proxy), drivers failing
 to yield, conflicts. No identification, no register, no penalties. Faces and
 plates pixelated; frames never stored.</p>
 <p><a class="cta" href="/en/dashboard.html">View the dashboard (synthetic data)</a>
    <a class="cta ghost" href="/en/contact.html">For government / For companies</a></p>
</section>
<section class="cols3">
 <div><h3>🔒 Privacy as the product</h3><p>Only aggregate counters ever reach disk.
 Identities are not retained; no register exists. Faces/plates pixelated at
 inference; no face embeddings; ephemeral track IDs live in RAM only.</p></div>
 <div><h3>📊 Metrics you can trust</h3><p>Every rate is normalized to actually
 observed time (coverage). Stream gaps render as “no data”, never as fake zeros.
 Camera failover is shown right on the dashboard.</p></div>
 <div><h3>🛠 Expertise, not a box</h3><p>The demonstrator sells competence:
 road-safety analytics deployments, CV/AI consulting, integrations.
 Author: Andrii Shramko — computer vision / VR / 3D specialist (Poland).</p></div>
</section>
<section>
 <h2>Who it is for</h2>
 <ul>
  <li><strong>Municipalities & road authorities</strong> — objective before/after
  data for infrastructure changes (refuge islands, lighting, raised crossings).</li>
  <li><strong>Integrators & smart-city vendors</strong> — behavioural analytics
  layer as a subcontract.</li>
  <li><strong>Research projects & EU grants</strong> — measurable pedestrian-safety
  indicators.</li>
 </ul>
</section>
<section>
 <h2>What we can already do</h2>
 <ul>
  <li>Count pedestrians and vehicles per hour (no camera calibration needed).</li>
  <li>Measure crosswalk occupancy and detect <strong>failure to yield</strong> to a
  pedestrian (topological event — a vehicle transits the crossing without stopping
  while a pedestrian is on it).</li>
  <li>Estimate the <strong>% of head-down pedestrians</strong> (a sampled attention
  proxy with a confidence interval — never an accusation of a specific person).</li>
  <li>Show hour×day trends, observation coverage, and camera failover status.</li>
  <li>Do it privately: faces/plates pixelated, zero frames on disk, zero register.</li>
 </ul>
 <h2>What we can add</h2>
 <ul>
  <li><strong>Conflict times in seconds (PET / TTC)</strong> and near-misses — on a
  metrically calibrated camera: a numeric “close-call” indicator before a collision happens.</li>
  <li><strong>Before/after reports</strong> for a specific intervention (refuge island,
  raised table, lighting, signals) — hard proof of whether the change worked.</li>
  <li><strong>Night-time and weather analysis</strong>, a city-wide ranking of the most
  dangerous crossings, threshold alerts.</li>
  <li><strong>Camera-based speed</strong> as a screening layer flagging candidates for a
  certified authority device — never as evidence.</li>
  <li>Integration with existing municipal cameras and dashboards.</li>
 </ul>
 <h2>How we change pedestrian safety</h2>
 <p>The causal chain is simple: <strong>measure → pinpoint the most dangerous crossings
 → recommend an intervention → measure its effect.</strong> Today, decisions about a
 refuge island, a raised table or better lighting are often made by intuition and
 without checking the result. We provide a continuous, objective, anonymous signal:
 where drivers fail to yield, where pedestrians are distracted, at which hours risk
 rises — and after an infrastructure change we show, in numbers, whether it actually
 dropped. That turns pedestrian safety from guesswork into a measurable, repeatable process.</p>
</section>
<section id="contact" class="card">
 <h2>Let's talk about your crossing</h2>
 <p>Get in touch — for a municipality, a company or a research project. I reply within 1–2 business days.</p>
 """ + lead_form_html("en") + """
</section>"""
    return head(lang, "index", JSONLD_HOME.replace("%BASE%", BASE)) + body + foot(lang)


def page_dashboard(lang):
    t = {"pl": {
        "h1": "Dashboard bezpieczeństwa przejścia (dane syntetyczne)",
        "note": ("Prezentowane dane są w całości <strong>syntetyczne</strong> — "
                 "służą demonstracji produktu. Luki = «brak danych» (nigdy zera). "
                 "Wskaźniki znormalizowane do czasu obserwacji."),
        "kpi": ["Pokrycie obserwacją", "Piesi / dzień (śr.)",
                "Nieustąpienia / dzień", "% głowa w dół (7 dni)"],
        "charts": ["Piesi na godzinę (14 dni, luki = brak danych)",
                   "% pieszych z głową w dół — proxy uwagi (dzienne, 95% CI)",
                   "Nieustąpienie pierwszeństwa i konflikty (dziennie)",
                   "Mapa cieplna: piesi wg godziny × dnia tygodnia",
                   "Stan kamer i failover"],
        "healthnote": "Pula failover obserwuje JEDNO przejście: primary + backup.",
    }, "en": {
        "h1": "Crossing safety dashboard (synthetic data)",
        "note": ("All data shown is fully <strong>synthetic</strong> — it exists to "
                 "demonstrate the product. Gaps = “no data” (never zeros). "
                 "Rates normalized to observed time."),
        "kpi": ["Observation coverage", "Pedestrians / day (avg)",
                "Failures to yield / day", "% head-down (7 days)"],
        "charts": ["Pedestrians per hour (14 days, gaps = no data)",
                   "% head-down pedestrians — attention proxy (daily, 95% CI)",
                   "Failure-to-yield & conflicts (daily)",
                   "Heatmap: pedestrians by hour × weekday",
                   "Camera health & failover"],
        "healthnote": "The failover pool watches ONE crossing: primary + backup.",
    }}[lang]
    kpis = "".join(f'<div class="kpi"><div class="kpi-v" id="kpi{i}">—</div>'
                   f'<div class="kpi-l">{l}</div></div>' for i, l in enumerate(t["kpi"]))
    return head(lang, "dashboard") + f"""
<h1>{t['h1']}</h1>
<p class="note">🧪 {t['note']}</p>
<div class="kpis">{kpis}</div>
<div class="card"><h2>{t['charts'][0]}</h2><canvas id="c_ped" height="90"></canvas></div>
<div class="card"><h2>{t['charts'][1]}</h2><canvas id="c_head" height="90"></canvas></div>
<div class="card"><h2>{t['charts'][2]}</h2><canvas id="c_yield" height="90"></canvas></div>
<div class="card"><h2>{t['charts'][3]}</h2><div id="heatmap"></div></div>
<div class="card"><h2>{t['charts'][4]}</h2>
 <p>{t['healthnote']}</p>
 <div id="pool"></div><table class="health" id="health"></table>
 <p class="fine" id="freshness"></p></div>
<script src="/assets/chart.umd.min.js"></script>
<script>window.DASH_LANG={'"pl"' if lang == 'pl' else '"en"'};</script>
<script src="/assets/dashboard.js"></script>
""" + foot(lang)


def page_how(lang):
    if lang == "pl":
        body = """
<h1>Jak to działa — i czego celowo NIE robimy</h1>
<ol class="pipeline">
 <li><strong>Źródło wideo</strong> — kamera z pisemną zgodą właściciela lub własna
 kamera. Pula failover (primary + backup) obserwuje jedno i to samo przejście.</li>
 <li><strong>Analiza w RAM</strong> — detekcja (model o licencji Apache-2.0),
 pikselizacja twarzy i tablic natychmiast po detekcji, tracking z efemerycznymi ID.</li>
 <li><strong>Topologia stref</strong> — liczniki przekroczeń linii (piesi, pojazdy),
 zajętość pasów przejścia, zdarzenie «pojazd nie ustąpił» wyznaczane topologicznie
 (przejazd przez strefę konfliktu bez zatrzymania, gdy pieszy jest na przejściu).</li>
 <li><strong>Na dysk — tylko liczniki</strong> — stats_bucket + coverage_bucket
 (rzeczywisty czas obserwacji) + camera_health. Żadnych klatek, żadnych embeddingów.</li>
 <li><strong>Dashboard</strong> — wykresy zagregowane, pokrycie, stan failover.</li>
</ol>
<h2>Uczciwe ograniczenia metody</h2>
<ul>
 <li><strong>% «głowa w dół»</strong> to <em>próbkowany wskaźnik proxy</em> uwagi
 z szerokim przedziałem ufności — NIE stwierdzenie «pieszy korzystał z telefonu».</li>
 <li><strong>PET / TTC (czasy konfliktów w sekundach)</strong> — wyłącznie na kamerze
 skalibrowanej metrycznie. Bez kalibracji pokazujemy bezwymiarowe zdarzenia konfliktu.</li>
 <li><strong>Ujęcia panoramiczne</strong> (piesi = kilka pikseli) → tylko zliczanie,
 metryki behawioralne są wyłączane automatycznie.</li>
 <li><strong>Noc, deszcz, śnieg</strong> obniżają czułość detekcji — dlatego każdy
 wskaźnik nosi ze sobą czas rzeczywistej obserwacji (coverage).</li>
 <li><strong>Pomiar prędkości</strong> z kamery to warstwa przesiewowa (screening) —
 nigdy dowód. Egzekwowanie wymaga certyfikowanych urządzeń organów państwa.</li>
</ul>"""
        extra = JSONLD_FAQ_PL
    else:
        body = """
<h1>How it works — and what we deliberately do NOT do</h1>
<ol class="pipeline">
 <li><strong>Video source</strong> — a camera with the owner's written permission or
 our own camera. The failover pool (primary + backup) watches one and the same crossing.</li>
 <li><strong>In-RAM analysis</strong> — detection (Apache-2.0-licensed model),
 pixelation of faces and plates right after detection, tracking with ephemeral IDs.</li>
 <li><strong>Zone topology</strong> — line-crossing counters (pedestrians, vehicles),
 crosswalk occupancy, “driver failed to yield” derived topologically (transit of the
 conflict zone without stopping while a pedestrian occupies the crossing).</li>
 <li><strong>Disk = counters only</strong> — stats_bucket + coverage_bucket (actually
 observed time) + camera_health. No frames, no embeddings.</li>
 <li><strong>Dashboard</strong> — aggregate charts, coverage, failover status.</li>
</ol>
<h2>Honest limitations</h2>
<ul>
 <li><strong>% “head-down”</strong> is a <em>sampled proxy indicator</em> of attention
 with a wide confidence interval — NOT a claim that “the pedestrian used a phone”.</li>
 <li><strong>PET / TTC (conflict times in seconds)</strong> — only on a metrically
 calibrated camera. Without calibration we show unitless conflict events.</li>
 <li><strong>Panoramic framings</strong> (pedestrians a few pixels tall) → counting
 only; behavioural metrics switch off automatically.</li>
 <li><strong>Night, rain, snow</strong> reduce detection sensitivity — which is why
 every metric carries its actually-observed time (coverage).</li>
 <li><strong>Camera-based speed</strong> is a screening layer — never evidence.
 Enforcement requires certified state instruments.</li>
</ul>"""
        extra = JSONLD_FAQ_EN
    return head(lang, "how-it-works", extra) + body + foot(lang)


def page_resources(lang):
    rows = [
        ("MVP (demo)", "2–4", "CPU offline", "≈ €0"),
        ("Pilot", "10–20", "1× dedykowany serwer (AX41)" if lang == "pl" else "1× dedicated server (AX41)", "~€40 / mies." if lang == "pl" else "~€40 / month"),
        ("Miasto" if lang == "pl" else "City", "~100", "1× GPU (GEX44, RTX 4000)", "~€184 / mies." if lang == "pl" else "~€184 / month"),
    ]
    tr = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>"
                 for a, b, c, d in rows)
    if lang == "pl":
        body = f"""
<h1>Zasoby i koszty — uczciwie</h1>
<p>Analiza działa na CPU; GPU staje się potrzebne dopiero od ~50 strumieni.
Poniżej rzędy wielkości (infrastruktura, bez pracy wdrożeniowej):</p>
<table class="tbl"><tr><th>Skala</th><th>Strumienie</th><th>Sprzęt</th><th>Koszt infra</th></tr>{tr}</table>
<h2>Co dostaje klient</h2>
<ul><li>Dashboard zagregowanych wskaźników bezpieczeństwa (PL/EN).</li>
<li>Raporty przed/po zmianie infrastruktury.</li>
<li>Wdrożenie privacy-by-design z dokumentacją (LIA/DPIA po stronie prawnej klienta
lub we współpracy).</li>
<li>Doradztwo computer vision / integracje (podwykonawstwo).</li></ul>
<p class="note">Wycena wdrożenia zależy od liczby przejść, jakości kamer i wymagań
prawnych — <a href="/contact.html">napisz</a>.</p>"""
    else:
        body = f"""
<h1>Resources & costs — honestly</h1>
<p>Inference runs on CPU; a GPU only becomes necessary at ~50 streams.
Orders of magnitude below (infrastructure only, excluding integration work):</p>
<table class="tbl"><tr><th>Scale</th><th>Streams</th><th>Hardware</th><th>Infra cost</th></tr>{tr}</table>
<h2>What the client gets</h2>
<ul><li>Aggregate safety-metrics dashboard (PL/EN).</li>
<li>Before/after reports for infrastructure changes.</li>
<li>Privacy-by-design deployment with documentation (LIA/DPIA on the client's legal
side or jointly).</li>
<li>Computer-vision consulting / integrations (subcontract).</li></ul>
<p class="note">Deployment pricing depends on the number of crossings, camera quality
and legal requirements — <a href="/en/contact.html">get in touch</a>.</p>"""
    return head(lang, "resources") + body + foot(lang)


def page_compliance(lang):
    if lang == "pl":
        body = """
<h1>Zgodność: RODO i AI Act — privacy by design</h1>
<p class="note">Nasza teza produktowa: <strong>tożsamości nie są utrwalane,
rejestr nie jest prowadzony, klatki nie są zapisywane, twarze i tablice są
pikselizowane, a egzekwowanie prawa pozostaje przy właściwym organie.</strong></p>
<h2>Jak projektujemy zgodność</h2>
<ul>
 <li><strong>Minimalizacja (art. 5 ust. 1 lit. c RODO):</strong> na dysk trafiają
 wyłącznie zagregowane liczniki na minutę; dane wyjściowe nie zawierają danych osobowych.</li>
 <li><strong>Pikselizacja twarzy/tablic na etapie analizy;</strong> klatka źródłowa
 istnieje wyłącznie ulotnie w pamięci RAM podczas przetwarzania i nie jest utrwalana.</li>
 <li><strong>Zero biometrii:</strong> twarz jest wyłącznie regionem do pikselizacji —
 nie liczymy i nie przechowujemy żadnych embeddingów ani wzorców biometrycznych.
 System nie wykonuje zdalnej identyfikacji biometrycznej (AI Act, art. 5).</li>
 <li><strong>Podstawa prawna przetwarzania w czasie analizy:</strong> prawnie
 uzasadniony interes (art. 6 ust. 1 lit. f RODO; cel = bezpieczeństwo ruchu) —
 z wykonaną oceną LIA oraz DPIA <em>przed</em> uruchomieniem na rzeczywistej kamerze.</li>
 <li><strong>Żadnych decyzji zautomatyzowanych wobec osób (art. 22)</strong> —
 wynik to statystyka infrastruktury, nie ocena człowieka.</li>
 <li><strong>Brak rejestru wykroczeń (art. 10 RODO)</strong> — system nie przypisuje
 zdarzeń osobom i nie tworzy list.</li>
 <li><strong>Retencja:</strong> liczniki zagregowane bez danych osobowych; logi
 techniczne rotowane automatycznie.</li>
</ul>
<h2>Rola i status</h2>
<p>Demonstrator prowadzi Andrii Shramko (JDG, NIP PL7543116302) jako
<strong>administrator danych (demo)</strong>. Wdrożenie produkcyjne u klienta
publicznego odbywa się na podstawie prawnej organu (art. 6 ust. 1 lit. e) i umowy
powierzenia (art. 28) — wtedy dostawca działa jako podmiot przetwarzający.</p>
<h2>Czego ten system NIE robi</h2>
<ul>
 <li>nie identyfikuje osób ani pojazdów, nie rozpoznaje twarzy;</li>
 <li>nie nakłada kar i nie wspiera nakładania kar;</li>
 <li>nie prowadzi żadnego rejestru zdarzeń przypisanych osobom;</li>
 <li>nie transmituje i nie archiwizuje wideo.</li>
</ul>"""
    else:
        body = """
<h1>Compliance: GDPR & AI Act — privacy by design</h1>
<p class="note">Our product thesis: <strong>identities are not retained, no register
is kept, frames are not stored, faces and plates are pixelated, and law enforcement
stays with the competent authority.</strong></p>
<h2>How compliance is designed in</h2>
<ul>
 <li><strong>Minimisation (GDPR Art. 5(1)(c)):</strong> only per-minute aggregate
 counters reach disk; the output data contains no personal data.</li>
 <li><strong>Pixelation of faces/plates at the analysis stage;</strong> the source
 frame exists only transiently in RAM during processing and is never persisted.</li>
 <li><strong>Zero biometrics:</strong> a face is only a region to pixelate — we never
 compute or store embeddings or biometric templates. The system performs no remote
 biometric identification (AI Act Art. 5).</li>
 <li><strong>Legal basis for the processing moment:</strong> legitimate interest
 (GDPR Art. 6(1)(f); purpose = road safety) — with an LIA and DPIA completed
 <em>before</em> any run on a real camera.</li>
 <li><strong>No automated decisions about individuals (Art. 22)</strong> — the output
 is infrastructure statistics, not an assessment of a person.</li>
 <li><strong>No register of offences (GDPR Art. 10)</strong> — the system attributes
 nothing to persons and keeps no lists.</li>
 <li><strong>Retention:</strong> aggregate counters carry no personal data; technical
 logs rotate automatically.</li>
</ul>
<h2>Role and status</h2>
<p>The demonstrator is run by Andrii Shramko (sole trader, VAT PL7543116302) as
<strong>data controller (demo)</strong>. A production deployment for a public client
runs on the authority's own legal basis (Art. 6(1)(e)) with a processing agreement
(Art. 28) — the supplier then acts as processor.</p>
<h2>What this system does NOT do</h2>
<ul>
 <li>does not identify persons or vehicles, no face recognition;</li>
 <li>does not impose penalties, nor support imposing them;</li>
 <li>keeps no register of events attributed to persons;</li>
 <li>does not stream or archive video.</li>
</ul>"""
    return head(lang, "compliance") + body + foot(lang)


def lead_form_html(lang):
    if lang == "pl":
        f = {"name": "Imię i nazwisko*", "org": "Organizacja", "role": "Rola",
             "seg": "Segment", "segs": ["Samorząd", "Firma", "Projekt UE / nauka", "Inne"],
             "email": "E-mail*", "msg": "Wiadomość*",
             "consent": ("Wyrażam zgodę na przetwarzanie podanych danych w celu obsługi "
                         "zapytania (administrator: Andrii Shramko, JDG; szczegóły w "
                         "<a href='/privacy.html'>polityce prywatności</a>).*"),
             "send": "Wyślij", "ok": "Dziękuję! Zapytanie dotarło — odezwę się wkrótce.",
             "err": "Nie udało się wysłać. Napisz proszę bezpośrednio: zmei116@gmail.com"}
    else:
        f = {"name": "Full name*", "org": "Organisation", "role": "Role",
             "seg": "Segment", "segs": ["Government", "Company", "EU project / research", "Other"],
             "email": "E-mail*", "msg": "Message*",
             "consent": ("I consent to the processing of the data provided in order to "
                         "handle this inquiry (controller: Andrii Shramko, sole trader; "
                         "details in the <a href='/en/privacy.html'>privacy policy</a>).*"),
             "send": "Send", "ok": "Thank you! Your inquiry arrived — I'll be in touch soon.",
             "err": "Sending failed. Please write directly: zmei116@gmail.com"}
    opts = "".join(f"<option>{s}</option>" for s in f["segs"])
    return f"""<form id="leadform" autocomplete="on">
 <label>{f['name']}<input name="name" type="text" required autocomplete="name"></label>
 <label>{f['org']}<input name="organization" type="text" autocomplete="organization"></label>
 <label>{f['role']}<input name="role" type="text" autocomplete="organization-title"></label>
 <label>{f['seg']}<select name="segment">{opts}</select></label>
 <label>{f['email']}<input name="email" type="email" required autocomplete="email"></label>
 <label>{f['msg']}<textarea name="message" rows="5" required></textarea></label>
 <input name="website" type="text" class="hp" tabindex="-1" autocomplete="off">
 <label class="consent"><input name="consent" type="checkbox" required value="yes"> <span>{f['consent']}</span></label>
 <input type="hidden" name="lang" value="{lang}">
 <button type="submit">{f['send']}</button>
</form>
<p id="formok" class="ok" style="display:none">✅ {f['ok']}</p>
<p id="formerr" class="err" style="display:none">❌ {f['err']}</p>
{FORM_JS}"""


def page_contact(lang):
    if lang == "pl":
        f = {"h1": "Kontakt", "lead": "Odpowiadam zwykle w 1–2 dni robocze.",
             "name": "Imię i nazwisko*", "org": "Organizacja", "role": "Rola",
             "seg": "Segment", "segs": ["Samorząd", "Firma", "Projekt UE / nauka", "Inne"],
             "email": "E-mail*", "msg": "Wiadomość*",
             "consent": ("Wyrażam zgodę na przetwarzanie podanych danych w celu "
                         "obsługi zapytania (administrator: Andrii Shramko, JDG; "
                         "szczegóły w <a href='/privacy.html'>polityce prywatności</a>).*"),
             "send": "Wyślij", "ok": "Dziękuję! Zapytanie dotarło — odezwę się wkrótce.",
             "err": "Nie udało się wysłać. Napisz proszę bezpośrednio: zmei116@gmail.com"}
    else:
        f = {"h1": "Contact", "lead": "I usually reply within 1–2 business days.",
             "name": "Full name*", "org": "Organisation", "role": "Role",
             "seg": "Segment", "segs": ["Government", "Company", "EU project / research", "Other"],
             "email": "E-mail*", "msg": "Message*",
             "consent": ("I consent to the processing of the data provided in order to "
                         "handle this inquiry (controller: Andrii Shramko, sole trader; "
                         "details in the <a href='/en/privacy.html'>privacy policy</a>).*"),
             "send": "Send", "ok": "Thank you! Your inquiry arrived — I'll be in touch soon.",
             "err": "Sending failed. Please write directly: zmei116@gmail.com"}
    body = f"""
<h1>{f['h1']}</h1>
<p>{f['lead']}</p>
{lead_form_html(lang)}
<p>E-mail: <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a> ·
LinkedIn: <a href="https://www.linkedin.com/in/andriishramko">andriishramko</a></p>"""
    return head(lang, "contact") + body + foot(lang)


def page_privacy(lang):
    if lang == "pl":
        body = """
<h1>Polityka prywatności i nota prawna</h1>
<h2>Administrator</h2>
<p>Andrii Shramko, jednoosobowa działalność gospodarcza (Polska), NIP PL7543116302,
e-mail: zmei116@gmail.com.</p>
<h2>Ta strona</h2>
<ul>
 <li>Nie używa cookies śledzących ani analityki behawioralnej.</li>
 <li>Wszystkie dane na dashboardzie są syntetyczne (demo) i nie dotyczą żadnych osób.</li>
 <li>Formularz kontaktowy przetwarza podane dane (imię, e-mail, treść) wyłącznie w celu
 obsługi zapytania — podstawa: art. 6 ust. 1 lit. b/f RODO; retencja: do 12 miesięcy od
 zamknięcia korespondencji; odbiorcą technicznym jest komunikator Telegram (powiadomienie
 o zapytaniu). Przysługuje Ci dostęp, sprostowanie, usunięcie, ograniczenie, sprzeciw
 oraz skarga do PUODO.</li>
</ul>
<h2>Demonstrator analityki</h2>
<p>Zasady privacy-by-design opisuje strona <a href="/compliance.html">Zgodność</a>.
Wynik działania systemu (liczniki zagregowane) nie zawiera danych osobowych. Żaden
wynik nie może być traktowany jako zarzut lub dowód wykroczenia wobec kogokolwiek.</p>"""
    else:
        body = """
<h1>Privacy policy & legal notice</h1>
<h2>Controller</h2>
<p>Andrii Shramko, sole trader (Poland), VAT PL7543116302,
e-mail: zmei116@gmail.com.</p>
<h2>This website</h2>
<ul>
 <li>Uses no tracking cookies and no behavioural analytics.</li>
 <li>All dashboard data is synthetic (demo) and relates to no persons.</li>
 <li>The contact form processes the submitted data (name, e-mail, message) solely to
 handle your inquiry — basis: GDPR Art. 6(1)(b)/(f); retention: up to 12 months after
 the correspondence closes; the technical recipient is the Telegram messenger (inquiry
 notification). You have the right of access, rectification, erasure, restriction,
 objection, and complaint to the Polish DPA (PUODO).</li>
</ul>
<h2>The analytics demonstrator</h2>
<p>Privacy-by-design principles are described on the <a href="/en/compliance.html">
Compliance</a> page. The system's output (aggregate counters) contains no personal
data. No output may be treated as an accusation of, or evidence for, any offence.</p>"""
    return head(lang, "privacy") + body + foot(lang)


CSS = """
:root{--navy:#12314f;--blue:#1668b0;--bg:#f6f8fa;--warn:#fff3cd;--txt:#20262c}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;
color:var(--txt);background:var(--bg);line-height:1.55}
.disclaimer{background:var(--warn);border-bottom:1px solid #e6d9a8;padding:.5rem 1rem;
font-size:.85rem;text-align:center}
header{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;justify-content:space-between;
padding:.7rem 1.2rem;background:var(--navy);color:#fff}
.brand a{color:#fff;text-decoration:none;font-weight:700;font-size:1.05rem}
.demo-badge{background:#e8b409;color:#1c1c1c;font-size:.65rem;font-weight:800;
border-radius:4px;padding:.1rem .4rem;margin-left:.5rem;vertical-align:middle}
nav{display:flex;flex-wrap:wrap;gap:.9rem}nav a{color:#d9e6f2;text-decoration:none;font-size:.9rem}
nav a:hover{color:#fff}.lang{border:1px solid #5b7f9f;border-radius:4px;padding:.05rem .45rem}
main{max-width:1000px;margin:0 auto;padding:1.2rem}
.hero h1{font-size:1.9rem;color:var(--navy);margin:.8rem 0 .4rem}
.lead{font-size:1.05rem}.cta{display:inline-block;background:var(--blue);color:#fff;
padding:.6rem 1.1rem;border-radius:6px;text-decoration:none;margin:.3rem .4rem .3rem 0}
.cta.ghost{background:#fff;color:var(--blue);border:1px solid var(--blue)}
.cols3{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin:1.4rem 0}
.cols3 div{background:#fff;border:1px solid #e2e8ee;border-radius:8px;padding:1rem}
.note{background:#eaf3fb;border-left:4px solid var(--blue);padding:.6rem .9rem;border-radius:4px}
.card{background:#fff;border:1px solid #e2e8ee;border-radius:8px;padding:1rem;margin:1rem 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.8rem;margin:1rem 0}
.kpi{background:#fff;border:1px solid #e2e8ee;border-radius:8px;padding:.8rem;text-align:center}
.kpi-v{font-size:1.6rem;font-weight:800;color:var(--navy)}.kpi-l{font-size:.8rem;color:#5a6672}
table.tbl,{border-collapse:collapse}table.tbl td,table.tbl th{border:1px solid #d7dee5;
padding:.5rem .7rem;text-align:left}table.tbl th{background:#eef3f8}
table.health{width:100%;border-collapse:collapse;font-size:.85rem}
table.health td{border-bottom:1px solid #edf1f5;padding:.3rem .5rem}
#heatmap{display:grid;grid-template-columns:repeat(25,1fr);gap:2px;font-size:.6rem}
#heatmap div{aspect-ratio:1;border-radius:2px;display:flex;align-items:center;justify-content:center}
form{display:grid;gap:.7rem;max-width:540px}label{display:grid;gap:.25rem;font-size:.9rem}
input,select,textarea{padding:.5rem;border:1px solid #c9d3dc;border-radius:6px;font:inherit;width:100%}
.hp{position:absolute;left:-6000px;height:1px;width:1px;opacity:0}
.consent{grid-template-columns:auto 1fr;align-items:start}.consent input{width:auto;margin-top:.25rem}
button{background:var(--blue);color:#fff;border:0;border-radius:6px;padding:.6rem 1.2rem;
font:inherit;cursor:pointer}button:disabled{opacity:.6}
.ok{color:#176b2c}.err{color:#a3251c}.fine{font-size:.78rem;color:#68737e}
.pipeline li{margin:.45rem 0}
footer{border-top:1px solid #dde4ea;background:#fff;margin-top:2rem;padding:1rem 1.2rem;
font-size:.85rem;color:#4c5760}footer a{color:var(--blue)}
@media(max-width:640px){.hero h1{font-size:1.4rem}#heatmap{font-size:.45rem}}
"""

ROBOTS = """User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

Sitemap: %BASE%/sitemap.xml
""".replace("%BASE%", BASE)

LLMS = """# Bezpieczne Przejścia / SafeCross

> Privacy-first, aggregate-only pedestrian-crossing safety analytics demonstrator
> (Poland). It is NOT law enforcement: it identifies no persons, keeps no register,
> imposes no penalties, and stores no video frames. All published dashboard data is
> synthetic. Author: Andrii Shramko (computer vision / VR / 3D specialist, Poland).

Facts:
- The system counts aggregate behaviours only: pedestrians/hour, vehicles/hour,
  % head-down pedestrians (sampled attention proxy with confidence intervals),
  drivers failing to yield (topological event), conflict events.
- Faces and licence plates are pixelated during analysis; frames exist only in RAM.
- No face embeddings are ever computed; track IDs are ephemeral (RAM only).
- Disk storage = aggregate counters only: stats_bucket, coverage_bucket
  (actually-observed seconds; gaps render as no-data, never zeros), camera_health.
- Camera failover: a pool of sources watches one and the same crossing.
- Detector licensing: Apache-2.0 (YOLOX / RT-DETR class); no AGPL components.
- Enforcement stays with competent authorities (Policja / GITD) — this is analytics.

Pages:
- %BASE%/ (PL), %BASE%/en/ (EN)
- %BASE%/dashboard.html — aggregate charts (synthetic demo data)
- %BASE%/how-it-works.html — method + honest limitations
- %BASE%/compliance.html — GDPR / AI Act design
- %BASE%/resources.html — deployment scale & costs
- %BASE%/contact.html — contact form

Contact: zmei116@gmail.com · https://www.linkedin.com/in/andriishramko
""".replace("%BASE%", BASE)


def sitemap():
    urls = []
    for lang in ("pl", "en"):
        p = T[lang]["prefix"]
        for page in PAGES:
            f = "" if page == "index" else page + ".html"
            urls.append(f"<url><loc>{BASE}{p}/{f}</loc></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls) + "</urlset>")


def main():
    if os.path.isdir(PUB):
        for entry in os.listdir(PUB):
            if entry in ("data", "assets"):
                continue
            p = os.path.join(PUB, entry)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(os.path.join(PUB, "assets"), exist_ok=True)
    os.makedirs(os.path.join(PUB, "en"), exist_ok=True)

    gen = {"index": page_index, "dashboard": page_dashboard, "how-it-works": page_how,
           "resources": page_resources, "compliance": page_compliance,
           "contact": page_contact, "privacy": page_privacy}
    n = 0
    for lang in ("pl", "en"):
        outdir = PUB if lang == "pl" else os.path.join(PUB, "en")
        for page, fn in gen.items():
            fname = "index.html" if page == "index" else page + ".html"
            with open(os.path.join(outdir, fname), "w", encoding="utf-8") as f:
                f.write(fn(lang))
            n += 1
    with open(os.path.join(PUB, "assets", "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    with open(os.path.join(PUB, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(ROBOTS)
    with open(os.path.join(PUB, "llms.txt"), "w", encoding="utf-8") as f:
        f.write(LLMS)
    with open(os.path.join(PUB, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap())
    print(f"built {n} pages + assets into {PUB}")


if __name__ == "__main__":
    main()
