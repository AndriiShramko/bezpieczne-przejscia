# -*- coding: utf-8 -*-
"""Static site generator — Bezpieczne Przejścia / SafeCross.

Dark, modern, fast. The MAIN page is a live command center: real camera
feed with real-time counting + a crowd-verified event feed. Decision-maker
statistics (sourced, Polish) lead the narrative. PL root + /en/.
"""
import os
import shutil

BASE = "https://patrol.flyreelstudio.eu"
ROOT = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(ROOT, "public")
PAGES = ["index", "how-it-works", "accuracy", "resources", "compliance", "contact", "privacy"]

T = {
    "pl": {
        "lang": "pl", "prefix": "",
        "brand": "Bezpieczne Przejścia",
        "disclaimer": ("Demonstrator technologiczny — pokazuje możliwości analizy wideo dla "
                       "bezpieczeństwa pieszych. NIE jest oficjalnym nadzorem ani egzekwowaniem "
                       "prawa, nie nakłada kar, nie identyfikuje osób (twarze i tablice są "
                       "rozmywane). Dane behawioralne mają charakter poglądowy i są weryfikowane "
                       "przez ludzi."),
        "meta_desc": ("Bezpieczne Przejścia — analiza wideo AI na żywo z prawdziwej kamery: "
                      "liczenie pieszych i pojazdów, wykrywanie sytuacji konfliktowych na przejściu, "
                      "weryfikacja przez ludzi. Od „2% ustępujących” (KRBRD 2015) do realnej "
                      "poprawy po 2021 — pokazujemy to na żywo. Demonstrator dla samorządów "
                      "i KRBRD, prywatność by design."),
        "nav": {"index": "Na żywo", "how-it-works": "Jak to działa",
                "accuracy": "Skuteczność", "resources": "Wdrożenie",
                "compliance": "Zgodność / RODO", "contact": "Kontakt", "privacy": "Prywatność"},
        "titles": {
            "index": "Bezpieczne Przejścia — analiza bezpieczeństwa przejść na żywo (AI + weryfikacja ludzi)",
            "how-it-works": "Jak to działa — pipeline, model, uczciwe ograniczenia",
            "accuracy": "Skuteczność i ryzyko błędów — uczciwa ocena techniczna",
            "resources": "Wdrożenie i koszty — dla samorządów, GDDKiA, KRBRD",
            "compliance": "Zgodność: RODO i AI Act — privacy by design",
            "contact": "Kontakt — Bezpieczne Przejścia",
            "privacy": "Polityka prywatności i nota prawna",
        },
    },
    "en": {
        "lang": "en", "prefix": "/en",
        "brand": "SafeCross",
        "disclaimer": ("Technology demonstrator — it shows what video analysis can do for "
                       "pedestrian safety. It is NOT official surveillance or law enforcement, "
                       "imposes no penalties and identifies no persons (faces and plates are "
                       "blurred). Behavioural readouts are indicative and are verified by humans."),
        "meta_desc": ("SafeCross — live AI video analysis from a real camera: pedestrian and "
                      "vehicle counting, crossing-conflict detection, human verification. From "
                      "'2% yielding' (KRBRD 2015) to real improvement after the 2021 law — shown "
                      "live. A demonstrator for road authorities, privacy by design."),
        "nav": {"index": "Live", "how-it-works": "How it works",
                "accuracy": "Accuracy", "resources": "Deployment",
                "compliance": "Compliance / GDPR", "contact": "Contact", "privacy": "Privacy"},
        "titles": {
            "index": "SafeCross — live pedestrian-crossing safety analysis (AI + human verification)",
            "how-it-works": "How it works — pipeline, model, honest limitations",
            "accuracy": "Accuracy & error risk — an honest technical assessment",
            "resources": "Deployment & costs — for road authorities",
            "compliance": "Compliance: GDPR & AI Act — privacy by design",
            "contact": "Contact — SafeCross",
            "privacy": "Privacy policy & legal notice",
        },
    },
}

JSONLD = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Person","name":"Andrii Shramko","jobTitle":"Computer Vision / VR / 3D specialist",
  "knowsAbout":["computer vision","pedestrian safety analytics","road safety","3D Gaussian Splatting","volumetric capture"],
  "sameAs":["https://www.linkedin.com/in/andriishramko"]},
 {"@type":"Organization","name":"Shramko Research Team","founder":"Andrii Shramko","vatID":"PL7543116302"},
 {"@type":"SoftwareApplication","name":"Bezpieczne Przejścia / SafeCross","applicationCategory":"Computer vision, road-safety analytics","operatingSystem":"Linux","offers":{"@type":"Offer","price":"0","priceCurrency":"EUR"}},
 {"@type":"WebSite","name":"Bezpieczne Przejścia / SafeCross","url":"%BASE%","inLanguage":["pl","en"]}
]}</script>"""

FAQ_PL = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
 {"@type":"Question","name":"Czy system identyfikuje ludzi lub pojazdy?","acceptedAnswer":{"@type":"Answer","text":"Nie. Twarze i tablice rejestracyjne są rozmywane na wejściu, kadry nie są przechowywane, nie powstają embeddingi twarzy. Zapisywane są tylko liczniki oraz zrzuty zdarzeń z rozmytymi danymi do weryfikacji."}},
 {"@type":"Question","name":"Czy AI sam decyduje, że doszło do naruszenia?","acceptedAnswer":{"@type":"Answer","text":"Nie. AI FLAGUJE kandydatów na zdarzenia; ostateczną ocenę „czy kierowca ustąpił” podejmują ludzie, potwierdzając lub odrzucając każdy zrzut. Z tych głosów liczymy realną trafność."}},
 {"@type":"Question","name":"Skąd biorą się dane na stronie?","acceptedAnswer":{"@type":"Answer","text":"Z prawdziwej publicznej kamery przejścia w Polsce, analizowanej na żywo modelem wizji komputerowej. Widać wideo z nałożonymi ramkami i licznikami."}}
]}</script>"""

FORM_JS = """
<script>(function(){var f=document.getElementById('leadform');if(!f)return;
f.addEventListener('submit',function(e){e.preventDefault();var b=f.querySelector('button');b.disabled=true;
var d=Object.fromEntries(new FormData(f).entries());
fetch('/api/lead',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
.then(function(r){if(!r.ok)throw 0;f.style.display='none';document.getElementById('formok').style.display='block';})
.catch(function(){b.disabled=false;document.getElementById('formerr').style.display='block';});});})();</script>"""

LIGHTBOX = """
<div id="lightbox" class="lightbox" style="display:none">
 <button class="lb-close" aria-label="close">✕</button>
 <button class="lb-prev" aria-label="prev">‹</button>
 <div class="lb-body"><div class="lb-stage"></div><div class="lb-info"></div></div>
 <button class="lb-next" aria-label="next">›</button>
</div>"""

VERIFY_PL = """
<section class="verify">
 <h2>Jak to działa: 3 modele, które współpracują</h2>
 <div class="pipe3">
  <div><b>1 · YOLOX — lokalnie, za darmo</b><span>Percepcja: wykrywa pieszych i pojazdy w każdej klatce. Szybki, ale „nie rozumie” sceny.</span></div>
  <div><b>2 · AI-analityk — Gemini Flash-Lite, grosze</b><span>Zrozumienie: raz opisuje przejście (pasy, sygnalizacja, kierunki), a potem ocenia KAŻDY epizod — werdykt + wyjaśnienie po polsku.</span></div>
  <div><b>3 · Ludzie — za darmo</b><span>Weryfikacja: potwierdzasz lub odrzucasz werdykt AI. Liczymy realną zgodność AI↔ludzie.</span></div>
 </div>
 <p class="note">Razem dają wynik zbliżony do drogiego, „nieograniczonego” agenta AI — ale za grosze. Zdarzenie
 powstaje TYLKO gdy pojazd <strong>w ruchu</strong> spotyka pieszego w strefie przejścia (auto stojące na czerwonym to nie zdarzenie).</p>
 <h2 class="mt">Sprawdź AI — kliknij zdarzenie, obejrzyj klip na pełnym ekranie, przejdź do kolejnych</h2>
 <div class="tabs-row">
  <button class="tab cur" data-tab="all">Wszystkie</button>
  <button class="tab" data-tab="violation">Naruszenia wg AI</button>
  <button class="tab" data-tab="speeding">Prędkość (szac.)</button>
  <button class="tab" data-tab="rejected">Odrzucone przez AI — sprawdź!</button>
  <button class="tab" data-tab="pending">Czekają na AI</button>
 </div>
 <div id="events" class="events"><p class="muted pad">Wczytuję zdarzenia…</p></div>
</section>
<section class="charts">
 <h2>Statystyka przejścia — na żywo z bazy danych</h2>
 <div class="chart-grid">
  <div class="chart-card"><h3>Ruch na godzinę (48h)</h3><div class="chart" id="chart-traffic"></div></div>
  <div class="chart-card"><h3>Rozkład prędkości pojazdów (orientacyjnie)</h3><div class="chart" id="chart-speed"></div></div>
  <div class="chart-card"><h3>Epizody i naruszenia wg AI (na godzinę)</h3><div class="chart" id="chart-events"></div></div>
 </div>
 <p class="dl">Pobierz pełną analitykę przejścia:
  <a class="btn ghost" href="/cv/report.html" target="_blank">Raport HTML</a>
  <a class="btn ghost" href="/cv/report.csv">Dane CSV</a></p>
</section>"""

VERIFY_EN = """
<section class="verify">
 <h2>How it works: 3 models that cooperate</h2>
 <div class="pipe3">
  <div><b>1 · YOLOX — local, free</b><span>Perception: finds pedestrians and vehicles in every frame. Fast, but it doesn't "understand" the scene.</span></div>
  <div><b>2 · AI analyst — Gemini Flash-Lite, pennies</b><span>Understanding: describes the crossing once (lanes, signals, directions), then judges EVERY episode — verdict + explanation.</span></div>
  <div><b>3 · Humans — free</b><span>Verification: you confirm or refute the AI verdict. We compute real AI↔human agreement.</span></div>
 </div>
 <p class="note">Together they approach an expensive "unlimited" AI agent — for pennies. An event is created ONLY when a
 <strong>moving</strong> vehicle meets a pedestrian in the crossing zone (a car stopped at a red light is not an event).</p>
 <h2 class="mt">Check the AI — click an event, watch the clip fullscreen, step through the rest</h2>
 <div class="tabs-row">
  <button class="tab cur" data-tab="all">All</button>
  <button class="tab" data-tab="violation">AI violations</button>
  <button class="tab" data-tab="speeding">Speeding (est.)</button>
  <button class="tab" data-tab="rejected">AI-rejected — double-check!</button>
  <button class="tab" data-tab="pending">Awaiting AI</button>
 </div>
 <div id="events" class="events"><p class="muted pad">Loading events…</p></div>
</section>
<section class="charts">
 <h2>Crossing statistics — live from the database</h2>
 <div class="chart-grid">
  <div class="chart-card"><h3>Traffic per hour (48h)</h3><div class="chart" id="chart-traffic"></div></div>
  <div class="chart-card"><h3>Vehicle speed distribution (indicative)</h3><div class="chart" id="chart-speed"></div></div>
  <div class="chart-card"><h3>Episodes & AI violations per hour</h3><div class="chart" id="chart-events"></div></div>
 </div>
 <p class="dl">Download the full crossing analytics:
  <a class="btn ghost" href="/cv/report.html" target="_blank">HTML report</a>
  <a class="btn ghost" href="/cv/report.csv">CSV data</a></p>
</section>"""

RESEARCH_PL = """
<section class="research">
 <h2>Open source · dla badaczy i integratorów</h2>
 <p class="lead">Cały projekt jest otwarty (Apache-2.0) — <strong>każdy może go powtórzyć, zweryfikować lub rozwinąć</strong>.
 To celowo tani, wielomodelowy system (lokalny detektor + tania LLM + weryfikacja ludzi), który zbliża się jakością
 do drogiego, „nieograniczonego” agenta AI, ale kosztuje grosze.</p>
 <div class="cols3">
  <div><h3>Co jest w repo</h3><ul><li>Serwis CV (YOLOX ONNX, trekking, strefy, epizody, klipy).</li>
   <li>Warstwa AI: scene-context + analiza zdarzeń (tania LLM).</li><li>Baza danych, API, panel admina kamer.</li>
   <li>Frontend, raporty, docker-compose z limitami zasobów.</li></ul></div>
  <div><h3>Dla badaczy</h3><ul><li>Zbieramy anonimowe wyjaśnienia AI + oceny ludzi → zbiór do badań nad
   surrogate-safety i „did-not-yield”.</li><li>Metoda przenośna na dowolne przejście (opisz scenę → analizuj).</li>
   <li>Uczciwy profil błędów i granic (patrz „Skuteczność”).</li></ul></div>
  <div><h3>Współpraca</h3><p><strong>Andrii Shramko</strong> — computer vision / VR / 3D (Polska).
   Wdrożenia, konsultacje, wspólne badania i granty (KRBRD, NCBR, Horizon).
   <br>✉ <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a>
   <br>in <a href="https://www.linkedin.com/in/andriishramko">linkedin.com/in/andriishramko</a>
   <br>⌥ <a href="https://github.com/AndriiShramko/bezpieczne-przejscia">github.com/AndriiShramko/bezpieczne-przejscia</a></p></div>
 </div>
</section>"""

RESEARCH_EN = """
<section class="research">
 <h2>Open source · for researchers and integrators</h2>
 <p class="lead">The whole project is open (Apache-2.0) — <strong>anyone can reproduce, verify or extend it</strong>.
 It is a deliberately cheap, multi-model system (local detector + cheap LLM + human verification) that approaches
 the quality of an expensive "unlimited" AI agent for pennies.</p>
 <div class="cols3">
  <div><h3>What's in the repo</h3><ul><li>CV service (YOLOX ONNX, tracking, zones, episodes, clips).</li>
   <li>AI layer: scene-context + per-event analysis (cheap LLM).</li><li>Database, API, camera admin panel.</li>
   <li>Frontend, reports, docker-compose with resource caps.</li></ul></div>
  <div><h3>For researchers</h3><ul><li>We collect anonymous AI explanations + human ratings → a dataset for
   surrogate-safety and "did-not-yield" research.</li><li>The method ports to any crossing (describe the scene → analyse).</li>
   <li>Honest error profile and limits (see "Accuracy").</li></ul></div>
  <div><h3>Collaborate</h3><p><strong>Andrii Shramko</strong> — computer vision / VR / 3D (Poland).
   Deployments, consulting, joint research and grants.
   <br>✉ <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a>
   <br>in <a href="https://www.linkedin.com/in/andriishramko">linkedin.com/in/andriishramko</a>
   <br>⌥ <a href="https://github.com/AndriiShramko/bezpieczne-przejscia">github.com/AndriiShramko/bezpieczne-przejscia</a></p></div>
 </div>
</section>"""


def head(lang, page, extra=""):
    t = T[lang]
    other = "en" if lang == "pl" else "pl"
    fname = "" if page == "index" else page + ".html"
    nav = "".join(
        f'<a href="{t["prefix"]}/{p if p != "index" else ""}{".html" if p != "index" else ""}"'
        f'{" class=cur" if p == page else ""}>{t["nav"][p]}</a>' for p in PAGES[:-1])
    return f"""<!DOCTYPE html>
<html lang="{t['lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t['titles'][page]}</title>
<meta name="description" content="{t['meta_desc']}">
<meta property="og:type" content="website">
<meta property="og:title" content="{t['titles'][page]}">
<meta property="og:description" content="{t['meta_desc']}">
<meta property="og:url" content="{BASE}{t['prefix']}/{fname}">
<meta property="og:locale" content="{'pl_PL' if lang == 'pl' else 'en_US'}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0a0e14">
<link rel="canonical" href="{BASE}{t['prefix']}/{fname}">
<link rel="alternate" hreflang="pl" href="{BASE}/{fname}">
<link rel="alternate" hreflang="en" href="{BASE}/en/{fname}">
<link rel="alternate" hreflang="x-default" href="{BASE}/{fname}">
<link rel="stylesheet" href="/assets/style.css">
{extra}
</head>
<body>
<header class="nav">
 <a class="logo" href="{t['prefix']}/">🚸 <span>{t['brand']}</span></a>
 <nav>{nav}</nav>
 <a class="lang" href="{T[other]['prefix']}/{fname}">{'EN' if lang == 'pl' else 'PL'}</a>
</header>
<div class="disclaimer">{t['disclaimer']}</div>
<main>"""


def foot(lang):
    t = T[lang]
    imp = ("Administrator danych (demo): Andrii Shramko, JDG, NIP PL7543116302"
           if lang == "pl" else
           "Data controller (demo): Andrii Shramko, sole trader (PL), VAT PL7543116302")
    return f"""</main>
<footer>
 <div class="foot-grid">
  <div><strong>🚸 {t['brand']}</strong><p class="muted">{t['disclaimer']}</p></div>
  <div>{imp}<br><a href="{t['prefix']}/privacy.html">{t['nav']['privacy']}</a> ·
   <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a> ·
   <a href="https://www.linkedin.com/in/andriishramko" rel="me">LinkedIn</a></div>
 </div>
 <p class="fine">Bezpieczne Przejścia / SafeCross · privacy-first road-safety analytics · Andrii Shramko / Shramko Research Team ·
  <a href="https://github.com/AndriiShramko/bezpieczne-przejscia">GitHub (Apache-2.0)</a></p>
</footer>
</body></html>"""


def stat_card(idv, label, sub=""):
    return (f'<div class="stat" id="{idv}"><div class="big">—</div>'
            f'<div class="lbl">{label}</div><div class="sub">{sub}</div></div>')


def partial(name):
    """Optional HTML partials (FAQ, About) kept as separate files — easier to
    edit than giant Python string constants."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "partials", name)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def page_index(lang):
    def livesec(lang):
        if lang == "pl":
            head_ = ("Kamera na żywo — AI liczy w czasie rzeczywistym", "Publiczna kamera przejścia (PL) · 24/7",
                     "Podgląd na żywo z analizą AI (twarze i tablice rozmyte)", "Kamera chwilowo niedostępna — ponawiam…",
                     "pieszych (sesja)", "pojazdów (sesja)", "zdarzeń", "zgodność AI↔ludzie", "● nagrywam epizod…")
        else:
            head_ = ("Live camera — AI analyses in real time", "Public crossing camera (PL) · 24/7",
                     "Live preview with AI analysis (faces and plates blurred)", "Camera temporarily unavailable — reconnecting…",
                     "pedestrians (session)", "vehicles (session)", "events", "AI↔human agreement", "● recording episode…")
        h2, src, alt, off, k1, k2, k3, k4, epi = head_
        return f"""
<section id="live" class="live-wrap">
 <div class="live-head">
  <h2>{h2}</h2>
  <span class="epi-badge" id="epi-badge" style="display:none">{epi}</span>
  <span class="live-badge off" id="live-badge">○ OFFLINE</span>
 </div>
 <p class="muted" id="live-source">{src}</p>
 <div class="live-stage">
  <img id="live-img" alt="{alt}">
  <div class="live-offline" id="live-offline"><span>{off}</span></div>
 </div>
 <div id="ticker" class="ticker"></div>
 <div class="stats">
  {stat_card("st-ped", k1)}
  {stat_card("st-veh", k2)}
  {stat_card("st-ev", k3)}
  {stat_card("st-acc", k4)}
 </div>
 <p class="muted small" id="st-inframe"></p>
</section>"""

    if lang == "pl":
        hero = """
<section class="hero">
 <div class="hero-copy">
  <div class="kicker">Analiza wideo na żywo · prawdziwa kamera · weryfikacja przez ludzi</div>
  <h1>W 2015 r. tylko <span class="hot">2%</span> kierowców zatrzymywało się przed pieszym dochodzącym do przejścia.</h1>
  <p class="lead">Tyle wykazały badania obserwacyjne dla KRBRD (Politechnika Gdańska i Krakowska, 2015) —
  pieszemu czekającemu przy krawężniku ustępował wtedy ledwie 1 na 5 kierowców. Od 1 czerwca 2021 r.
  pieszy wchodzący na pasy ma pierwszeństwo — i liczba zabitych na przejściach spadła o 23,6%
  (KRBRD, 2019–2023). Nasza kamera pokazuje tę zmianę na żywo: naruszenia są dziś wyjątkiem,
  a każdy wykryty konflikt ocenia AI i weryfikują ludzie — bo nawet jeden to o jeden za dużo.</p>
  <div class="hero-cta">
   <a class="btn" href="#live">Zobacz kamerę na żywo ↓</a>
   <a class="btn ghost" href="#kontakt">Dla samorządu / KRBRD</a>
  </div>
 </div>
 <aside class="hero-facts">
  <div class="fact"><b>458</b><span>pieszych zginęło w 2023 — ok. 1 na 4 ofiary na drogach</span></div>
  <div class="fact"><b>131</b><span>z nich na oznakowanych przejściach (2023)</span></div>
  <div class="fact"><b>2,57 mln zł</b><span>jednostkowy koszt jednej ofiary śmiertelnej (KRBRD)</span></div>
 </aside>
</section>"""
        why = """
<section class="hooks">
 <h2>Dlaczego to ważne dla samorządu i KRBRD</h2>
 <div class="hook-grid">
  <div class="hook"><b>52 mld zł / rok</b><p>tyle kosztują Polskę wypadki drogowe — ok. 1,7% PKB.
  Jedno naprawione przejście potrafi zwrócić koszt całego programu.<span class="src">KRBRD, PANDORA 2022</span></p></div>
  <div class="hook"><b>−23,6%</b><p>spadek liczby ZABITYCH pieszych na przejściach po nowelizacji z 2021 r.
  (wypadki −7,8%). Działa — trzeba dokończyć.<span class="src">KRBRD 2019–2023</span></p></div>
  <div class="hook"><b>12,2 / mln</b><p>śmiertelność pieszych na milion mieszkańców — jedna z najwyższych w UE-27.
  <span class="src">Polskie Obserwatorium BRD</span></p></div>
  <div class="hook"><b>Vision Zero</b><p>UE: −50% ofiar do 2030, zero do 2050 (Safe System).
  Dostarczamy obiektywne dane, których brakuje w decyzjach o infrastrukturze.<span class="src">EU Road Safety Framework</span></p></div>
 </div>
</section>"""
        contact = f"""
<section id="kontakt" class="contact">
 <h2>Porozmawiajmy o Twoim przejściu</h2>
 <p class="lead">Dla samorządu, zarządu dróg, firmy lub projektu badawczego. Odpowiadam w 1–2 dni robocze.</p>
 {lead_form_html("pl")}
</section>"""
        verify, research = VERIFY_PL, RESEARCH_PL
    else:
        hero = """
<section class="hero">
 <div class="hero-copy">
  <div class="kicker">Live video analysis · real camera · human verification</div>
  <h1>In 2015, only <span class="hot">2%</span> of drivers stopped for a pedestrian approaching a crossing.</h1>
  <p class="lead">That is what observational research for Poland's National Road Safety Council found
  (Gdańsk &amp; Kraków Universities of Technology, 2015) — and barely 1 in 5 drivers yielded to someone
  waiting at the curb. Since 1 June 2021 the law gives priority to pedestrians entering the crossing,
  and deaths on crossings have fallen by 23.6% (KRBRD, 2019–2023). Our live camera shows that change
  in real time: violations are now the exception — and AI plus human review catches every one.</p>
  <div class="hero-cta">
   <a class="btn" href="#live">Watch the live camera ↓</a>
   <a class="btn ghost" href="#kontakt">For government</a>
  </div>
 </div>
 <aside class="hero-facts">
  <div class="fact"><b>458</b><span>pedestrians killed in 2023 — ~1 in 4 of all road deaths</span></div>
  <div class="fact"><b>131</b><span>of them on marked crossings (2023)</span></div>
  <div class="fact"><b>PLN 2.57M</b><span>unit cost of a single road fatality (KRBRD)</span></div>
 </aside>
</section>"""
        why = """
<section class="hooks">
 <h2>Why this matters to government</h2>
 <div class="hook-grid">
  <div class="hook"><b>PLN 52bn / yr</b><p>the cost of road crashes to Poland — ~1.7% of GDP.
  One fixed crossing can pay back the whole program.<span class="src">KRBRD, PANDORA 2022</span></p></div>
  <div class="hook"><b>−23.6%</b><p>drop in pedestrian DEATHS on crossings after the 2021 law
  (crashes −7.8%). It works — let's finish the job.<span class="src">KRBRD 2019–2023</span></p></div>
  <div class="hook"><b>12.2 / M</b><p>pedestrian death rate per million residents — among the worst in the EU-27.
  <span class="src">Polish Road Safety Observatory</span></p></div>
  <div class="hook"><b>Vision Zero</b><p>EU: −50% deaths by 2030, zero by 2050 (Safe System).
  We supply the objective data missing from today's infrastructure decisions.<span class="src">EU Road Safety Framework</span></p></div>
 </div>
</section>"""
        contact = f"""
<section id="kontakt" class="contact">
 <h2>Let's talk about your crossing</h2>
 <p class="lead">For a municipality, road authority, company or research project. I reply within 1–2 business days.</p>
 {lead_form_html("en")}
</section>"""
        verify, research = VERIFY_EN, RESEARCH_EN
    extra = (JSONLD.replace("%BASE%", BASE) + FAQ_PL +
             '\n<script defer src="/assets/live.js"></script>')
    return (head(lang, "index", extra) + hero + livesec(lang) + verify + why
            + research + partial(f"about_{lang}.html") + partial(f"faq_{lang}.html")
            + contact + LIGHTBOX + foot(lang))


def page_how(lang):
    if lang == "pl":
        body = """
<h1>Jak to działa — i czego celowo NIE robimy</h1>
<ol class="pipeline">
 <li><strong>Źródło:</strong> publiczna kamera przejścia (24/7). Analiza działa na żywo; kadr istnieje tylko w pamięci.</li>
 <li><strong>Detekcja:</strong> model <code>YOLOX</code> (licencja Apache-2.0, bez AGPL) na CPU wykrywa pieszych i pojazdy.</li>
 <li><strong>Prywatność:</strong> twarze i tablice są rozmywane natychmiast po detekcji, przed czymkolwiek innym.</li>
 <li><strong>Śledzenie:</strong> efemeryczne ID w pamięci — liczymy nowych pieszych/pojazdy, bez zapisywania kadrów.</li>
 <li><strong>Zdarzenia:</strong> gdy pojazd i pieszy są jednocześnie w strefie przejścia — flagujemy kandydata i zapisujemy rozmyty zrzut.</li>
 <li><strong>Weryfikacja ludzi:</strong> użytkownicy potwierdzają/odrzucają. Realną trafność liczymy z głosów.</li>
</ol>
<p class="note">To <strong>narzędzie przesiewowe</strong>, nie przyrząd pomiarowy. Liczby to estymaty,
zdarzenia to kandydaci potwierdzani przez ludzi. Nigdy nie podajemy „wskaźnika naruszeń” jako twardej liczby —
szczegóły na stronie <a href="/accuracy.html">Skuteczność</a>.</p>"""
        extra = FAQ_PL
    else:
        body = """
<h1>How it works — and what we deliberately do NOT do</h1>
<ol class="pipeline">
 <li><strong>Source:</strong> a public 24/7 crossing camera. Analysis runs live; the frame exists only in RAM.</li>
 <li><strong>Detection:</strong> a <code>YOLOX</code> model (Apache-2.0, no AGPL) on CPU finds pedestrians and vehicles.</li>
 <li><strong>Privacy:</strong> faces and plates are blurred immediately after detection, before anything else.</li>
 <li><strong>Tracking:</strong> ephemeral in-RAM IDs — we count new pedestrians/vehicles, storing no frames.</li>
 <li><strong>Events:</strong> when a vehicle and a pedestrian are in the crossing zone at once, we flag a candidate and save a blurred snapshot.</li>
 <li><strong>Human verification:</strong> users confirm/refute. True accuracy is computed from the votes.</li>
</ol>
<p class="note">This is a <strong>screening tool</strong>, not a measurement instrument. Counts are estimates,
events are human-confirmed candidates. We never state a "violation rate" as a hard number — see
<a href="/en/accuracy.html">Accuracy</a>.</p>"""
        extra = FAQ_PL
    return head(lang, "how-it-works", extra) + body + foot(lang)


def page_accuracy(lang):
    if lang == "pl":
        body = """
<h1>Skuteczność i ryzyko błędów — uczciwie</h1>
<p class="note"><strong>Najważniejsze:</strong> to narzędzie przesiewowe. Z jednej nieskalibrowanej
kamery nie da się podać obronnego „wskaźnika naruszeń”. Dlatego: liczby podajemy jako estymaty,
zdarzenia jako kandydatów, a prawdziwą trafność liczymy z weryfikacji ludzi.</p>
<h2>Gdzie AI się myli i dlaczego</h2>
<ul>
 <li><strong>Przesłonięcia:</strong> na przejściu trajektorie się przecinają — pieszy zasłonięty przez pojazd
 to najtrudniejszy przypadek; tracker gubi ID, licznik zaniża.</li>
 <li><strong>Perspektywa jednej kamery:</strong> skrót perspektywiczny sprawia, że pojazd jeszcze przed pasami
 „nakłada się” w obrazie na strefę przejścia — to <strong>główne źródło fałszywych alarmów</strong>.</li>
 <li><strong>Małe obiekty / panorama:</strong> daleki pieszy ma kilkadziesiąt pikseli — skuteczność dla małych
 obiektów bywa 2–3× niższa (COCO).</li>
 <li><strong>Noc, deszcz, odblaski:</strong> obraz spoza rozkładu treningowego — gwałtowny spadek trafności; te okresy traktujemy jako niskiej pewności.</li>
 <li><strong>Brak metryki (głębi):</strong> bez homografii pracujemy w pikselach — stąd niepewne prędkości i „czy zdążył zahamować”.</li>
</ul>
<h2>Czego się realnie spodziewać</h2>
<ul>
 <li>Detekcja pieszych/pojazdów w dzień, dobre ujęcie: użyteczna, ale niedoskonała (małe obiekty i tłok obniżają recall).</li>
 <li>Zdarzenie „nie ustąpił” z pojedynczej kamery bez kalibracji: <strong>dużo fałszywych alarmów</strong> — dlatego weryfikacja ludzi jest obowiązkowa.</li>
</ul>
<h2>Jak liczymy prawdziwą trafność</h2>
<p>Każde sflagowane zdarzenie ma zrzut. Ludzie głosują potwierdź/odrzuć. Precyzja = potwierdzone / ocenione.
Ten wskaźnik widać na żywo na stronie głównej — rośnie wiarygodnie w miarę głosów.</p>
<h2>Co doprowadzi do maksimum</h2>
<ol>
 <li><strong>Kalibracja metryczna (homografia)</strong> — największy zysk: odblokowuje prędkość, PET/TTC, poprawne „nie ustąpił”.</li>
 <li><strong>Wyższe fps + wygładzanie czasowe</strong> — mniej zgubionych ID.</li>
 <li><strong>Większy model / dobór kamery</strong> (tight framing) — wyższy recall.</li>
 <li><strong>Aktywne uczenie z głosów ludzi</strong> — model uczy się na własnych błędach.</li>
 <li><strong>Ograniczenie twierdzeń do tego, co pewne</strong> — uczciwość jako przewaga przed audytem.</li>
</ol>"""
    else:
        body = """
<h1>Accuracy & error risk — honestly</h1>
<p class="note"><strong>Bottom line:</strong> this is a screening tool. From one uncalibrated camera you
cannot state a defensible "violation rate". So: counts are estimates, events are candidates, and true
accuracy is computed from human verification.</p>
<h2>Where the AI is wrong, and why</h2>
<ul>
 <li><strong>Occlusion:</strong> at a crossing trajectories intersect — a pedestrian hidden by a vehicle is
 the hardest case; the tracker loses the ID, the count undershoots.</li>
 <li><strong>Single-camera perspective:</strong> foreshortening makes a vehicle still short of the stop line
 <em>appear</em> to overlap the crossing in image space — the <strong>biggest source of false positives</strong>.</li>
 <li><strong>Small objects / panorama:</strong> a distant pedestrian is tens of pixels — small-object accuracy is
 routinely 2–3× lower (COCO).</li>
 <li><strong>Night, rain, glare:</strong> out-of-distribution imagery — a steep accuracy cliff; we treat those periods as low-confidence.</li>
 <li><strong>No metric depth:</strong> without homography we work in pixels — hence uncertain speeds and "did it stop in time".</li>
</ul>
<h2>What to realistically expect</h2>
<ul>
 <li>Daytime pedestrian/vehicle detection on a good view: useful but imperfect (small objects and crowding lower recall).</li>
 <li>"Failure to yield" from a single uncalibrated camera: <strong>many false positives</strong> — which is why human verification is mandatory.</li>
</ul>
<h2>How we compute true accuracy</h2>
<p>Every flagged event has a snapshot. Humans vote confirm/refute. Precision = confirmed / judged.
That figure is shown live on the home page and grows trustworthy as votes accumulate.</p>
<h2>What pushes it to the maximum</h2>
<ol>
 <li><strong>Metric calibration (homography)</strong> — biggest win: unlocks speed, PET/TTC, correct "did not yield".</li>
 <li><strong>Higher fps + temporal smoothing</strong> — fewer ID switches.</li>
 <li><strong>Bigger model / camera choice</strong> (tight framing) — higher recall.</li>
 <li><strong>Active learning from human votes</strong> — the model learns from its own mistakes.</li>
 <li><strong>Restricting claims to what's reliable</strong> — honesty as an advantage in front of an audit.</li>
</ol>"""
    return head(lang, "accuracy") + body + foot(lang)


def page_resources(lang):
    rows = [("MVP (demo)", "1–4", "CPU offline / 1 mały serwer" if lang == "pl" else "CPU / 1 small server", "≈ €0–40 / mies." if lang == "pl" else "≈ €0–40 / mo"),
            ("Pilot", "10–20", "1× serwer dedykowany (AX41)", "~€40 / mies." if lang == "pl" else "~€40 / mo"),
            ("Miasto" if lang == "pl" else "City", "~100", "1× GPU (GEX44, RTX 4000)", "~€184 / mies." if lang == "pl" else "~€184 / mo")]
    tr = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in rows)
    if lang == "pl":
        body = f"""
<h1>Wdrożenie i koszty — dla samorządów, GDDKiA, KRBRD</h1>
<p>Analiza działa na CPU; GPU potrzebny dopiero od ~50 strumieni. Rzędy wielkości (sama infrastruktura):</p>
<table class="tbl"><tr><th>Skala</th><th>Strumienie</th><th>Sprzęt</th><th>Koszt</th></tr>{tr}</table>
<h2>Ścieżka do ЛПР</h2>
<ul>
 <li><strong>KRBRD / Sekretariat</strong> (Min. Infrastruktury) — cel: −50% do 2030; dostarczamy metodę skalowalną i spójną z Safe System.</li>
 <li><strong>ZDM / zarząd dróg miejskich</strong> — ranking najgroźniejszych przejść i uzasadnienie budżetu.</li>
 <li><strong>Prezydent / rada miasta</strong> — bezpieczeństwo mieszkańców jako widoczny efekt + gotowość pod granty.</li>
 <li><strong>GDDKiA / GITD / Policja (SEWIK)</strong> — obiektywne dane behawioralne, których dziś brak.</li>
</ul>
<p class="note">Wycena wdrożenia zależy od liczby przejść i jakości kamer — <a href="/#kontakt">napisz</a>.</p>"""
    else:
        body = f"""
<h1>Deployment & costs — for road authorities</h1>
<p>Analysis runs on CPU; a GPU is only needed from ~50 streams. Orders of magnitude (infrastructure only):</p>
<table class="tbl"><tr><th>Scale</th><th>Streams</th><th>Hardware</th><th>Cost</th></tr>{tr}</table>
<h2>Path to decision-makers</h2>
<ul>
 <li><strong>National road-safety council</strong> — target −50% by 2030; we provide a scalable method aligned with Safe System.</li>
 <li><strong>City road authority</strong> — a ranking of the most dangerous crossings and budget justification.</li>
 <li><strong>Mayor / city council</strong> — resident safety as a visible win + grant-readiness.</li>
 <li><strong>National roads / enforcement / police crash DB</strong> — objective behavioural data that's missing today.</li>
</ul>
<p class="note">Deployment pricing depends on crossings and camera quality — <a href="/en/#kontakt">get in touch</a>.</p>"""
    return head(lang, "resources") + body + foot(lang)


def page_compliance(lang):
    if lang == "pl":
        body = """
<h1>Zgodność: RODO i AI Act — privacy by design</h1>
<p class="note">Teza: <strong>twarze i tablice rozmywane, kadry nieprzechowywane, brak identyfikacji i rejestru;
zapisywane tylko liczniki i rozmyte zrzuty zdarzeń do weryfikacji. Egzekwowanie zostaje przy organie.</strong></p>
<ul>
 <li><strong>Rozmycie twarzy/tablic</strong> na etapie analizy; kadr istnieje tylko ulotnie w RAM.</li>
 <li><strong>Zero biometrii:</strong> twarz to jedynie region do rozmycia — bez embeddingów (AI Act art. 5).</li>
 <li><strong>Minimalizacja (art. 5 RODO):</strong> na dysk trafiają liczniki + rozmyte zrzuty zdarzeń, bez danych osobowych w wyniku.</li>
 <li><strong>Podstawa przetwarzania w chwili analizy:</strong> uzasadniony interes (art. 6 ust. 1 lit. f, cel = bezpieczeństwo ruchu) — LIA + DPIA przed wdrożeniem produkcyjnym na własnej/permissioned kamerze.</li>
 <li><strong>Brak decyzji zautomatyzowanych wobec osób (art. 22)</strong> i brak rejestru wykroczeń (art. 10).</li>
 <li><strong>Rola:</strong> Andrii Shramko (JDG, NIP PL7543116302) jako administrator (demo); wdrożenie u organu = podmiot przetwarzający (art. 28) na jego podstawie prawnej.</li>
</ul>
<p class="note">Ten demonstrator działa na publicznej kamerze w celach pokazu technologii; wdrożenie klienckie
wymaga zgody właściciela kamery i podpisu prawnika (LIA/DPIA).</p>"""
    else:
        body = """
<h1>Compliance: GDPR & AI Act — privacy by design</h1>
<p class="note">Thesis: <strong>faces and plates blurred, frames not stored, no identification and no register;
only counters and blurred event snapshots are saved for verification. Enforcement stays with the authority.</strong></p>
<ul>
 <li><strong>Face/plate blur</strong> at the analysis stage; the frame exists only transiently in RAM.</li>
 <li><strong>Zero biometrics:</strong> a face is only a region to blur — no embeddings (AI Act Art. 5).</li>
 <li><strong>Minimisation (GDPR Art. 5):</strong> disk holds counters + blurred event snapshots, no personal data in the output.</li>
 <li><strong>Basis for the processing moment:</strong> legitimate interest (Art. 6(1)(f), road safety) — LIA + DPIA before any production deployment on an own/permissioned camera.</li>
 <li><strong>No automated decisions about individuals (Art. 22)</strong> and no register of offences (Art. 10).</li>
 <li><strong>Role:</strong> Andrii Shramko (sole trader, VAT PL7543116302) as controller (demo); an authority deployment = processor (Art. 28) on their legal basis.</li>
</ul>
<p class="note">This demonstrator runs on a public camera to show the technology; a client deployment requires
the camera owner's permission and a lawyer's sign-off (LIA/DPIA).</p>"""
    return head(lang, "compliance") + body + foot(lang)


def lead_form_html(lang):
    if lang == "pl":
        f = {"name": "Imię i nazwisko*", "org": "Organizacja", "role": "Rola",
             "seg": "Segment", "segs": ["Samorząd / zarząd dróg", "KRBRD / administracja", "Firma", "Projekt UE / nauka", "Inne"],
             "email": "E-mail*", "msg": "Wiadomość*",
             "consent": ("Zgadzam się na przetwarzanie danych w celu obsługi zapytania (administrator: "
                         "Andrii Shramko, JDG; <a href='/privacy.html'>polityka prywatności</a>).*"),
             "send": "Wyślij", "ok": "Dziękuję! Zapytanie dotarło — odezwę się wkrótce.",
             "err": "Nie udało się wysłać. Napisz: zmei116@gmail.com"}
    else:
        f = {"name": "Full name*", "org": "Organisation", "role": "Role",
             "seg": "Segment", "segs": ["Municipality / road authority", "National road-safety body", "Company", "EU project / research", "Other"],
             "email": "E-mail*", "msg": "Message*",
             "consent": ("I consent to processing my data to handle this inquiry (controller: Andrii "
                         "Shramko, sole trader; <a href='/en/privacy.html'>privacy policy</a>).*"),
             "send": "Send", "ok": "Thank you! Your inquiry arrived — I'll be in touch soon.",
             "err": "Sending failed. Write to: zmei116@gmail.com"}
    opts = "".join(f"<option>{s}</option>" for s in f["segs"])
    return f"""<form id="leadform" autocomplete="on">
 <label>{f['name']}<input name="name" type="text" required autocomplete="name"></label>
 <label>{f['org']}<input name="organization" type="text" autocomplete="organization"></label>
 <label>{f['role']}<input name="role" type="text" autocomplete="organization-title"></label>
 <label>{f['seg']}<select name="segment">{opts}</select></label>
 <label>{f['email']}<input name="email" type="email" required autocomplete="email"></label>
 <label class="wide">{f['msg']}<textarea name="message" rows="4" required></textarea></label>
 <input name="website" type="text" class="hp" tabindex="-1" autocomplete="off">
 <label class="consent wide"><input name="consent" type="checkbox" required value="yes"><span>{f['consent']}</span></label>
 <input type="hidden" name="lang" value="{lang}">
 <button type="submit">{f['send']}</button>
</form>
<p id="formok" class="ok" style="display:none">✅ {f['ok']}</p>
<p id="formerr" class="err" style="display:none">❌ {f['err']}</p>
{FORM_JS}"""


def page_contact(lang):
    t = ({"h1": "Kontakt", "lead": "Odpowiadam zwykle w 1–2 dni robocze."} if lang == "pl"
         else {"h1": "Contact", "lead": "I usually reply within 1–2 business days."})
    body = f"""<h1>{t['h1']}</h1><p class="lead">{t['lead']}</p>{lead_form_html(lang)}
<p class="muted">E-mail: <a href="mailto:zmei116@gmail.com">zmei116@gmail.com</a> ·
LinkedIn: <a href="https://www.linkedin.com/in/andriishramko">andriishramko</a></p>"""
    return head(lang, "contact") + body + foot(lang)


def page_privacy(lang):
    if lang == "pl":
        body = """
<h1>Polityka prywatności i nota prawna</h1>
<h2>Administrator</h2><p>Andrii Shramko, JDG (Polska), NIP PL7543116302, zmei116@gmail.com.</p>
<h2>Strona i demonstrator</h2>
<ul><li>Brak cookies śledzących i analityki behawioralnej.</li>
<li>Analiza działa na publicznej kamerze; twarze i tablice są rozmywane, kadry nie są przechowywane,
nie powstają embeddingi. Zapisywane są liczniki i rozmyte zrzuty zdarzeń do weryfikacji — bez danych osobowych w wyniku.</li>
<li>Głosy weryfikacyjne (potwierdź/odrzuć) zapisujemy anonimowo (bez danych osobowych) do liczenia trafności.</li>
<li>Formularz kontaktowy: dane (imię, e-mail, treść) przetwarzane wyłącznie do obsługi zapytania —
art. 6 ust. 1 lit. b/f RODO; retencja do 12 mies.; powiadomienie technicznie przez Telegram. Prawa: dostęp,
sprostowanie, usunięcie, sprzeciw, skarga do PUODO.</li></ul>
<p class="note">Żaden wynik nie może być traktowany jako zarzut lub dowód wykroczenia wobec kogokolwiek.</p>"""
    else:
        body = """
<h1>Privacy policy & legal notice</h1>
<h2>Controller</h2><p>Andrii Shramko, sole trader (Poland), VAT PL7543116302, zmei116@gmail.com.</p>
<h2>Site & demonstrator</h2>
<ul><li>No tracking cookies and no behavioural analytics.</li>
<li>Analysis runs on a public camera; faces and plates are blurred, frames are not stored, no embeddings are
computed. Only counters and blurred event snapshots are saved for verification — no personal data in the output.</li>
<li>Verification votes (confirm/refute) are stored anonymously (no personal data) to compute accuracy.</li>
<li>Contact form: data (name, e-mail, message) processed solely to handle the inquiry — GDPR Art. 6(1)(b)/(f);
retention up to 12 months; technical notification via Telegram. Rights: access, rectification, erasure,
objection, complaint to the Polish DPA.</li></ul>
<p class="note">No output may be treated as an accusation of, or evidence for, any offence.</p>"""
    return head(lang, "privacy") + body + foot(lang)


CSS = """
:root{--bg:#0a0e14;--bg2:#0f1620;--panel:#141c28;--panel2:#1a2432;--line:#233044;
--tx:#e6edf3;--mut:#8b97a7;--acc:#2ee6a6;--acc2:#37b6ff;--hot:#ff5d6c;--warn:#ffcf5c}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--tx);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,sans-serif;
-webkit-font-smoothing:antialiased}
a{color:var(--acc2);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:clamp(1.7rem,4vw,2.6rem);line-height:1.12;letter-spacing:-.02em;margin:.2em 0 .5em}
h2{font-size:clamp(1.3rem,2.6vw,1.9rem);letter-spacing:-.01em;margin:1.8rem 0 .8rem}
h3{font-size:1.1rem;margin:.6rem 0}
.mt{margin-top:2.6rem}
main{max-width:1160px;margin:0 auto;padding:1.4rem}
.muted{color:var(--mut)}.small{font-size:.85rem}.pad{padding:1rem}
.nav{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:1rem;
padding:.7rem 1.4rem;background:rgba(10,14,20,.85);backdrop-filter:blur(10px);
border-bottom:1px solid var(--line)}
.logo{color:var(--tx);font-weight:800;font-size:1.05rem;display:flex;gap:.4rem;align-items:center}
.logo span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.nav nav{display:flex;gap:1.1rem;flex-wrap:wrap;margin-left:auto;font-size:.9rem}
.nav nav a{color:var(--mut)}.nav nav a:hover,.nav nav a.cur{color:var(--tx);text-decoration:none}
.lang{border:1px solid var(--line);border-radius:7px;padding:.15rem .5rem;color:var(--tx);font-size:.8rem}
.disclaimer{background:linear-gradient(90deg,#20160a,#1a1206);color:#e8c98a;
font-size:.82rem;padding:.55rem 1.4rem;text-align:center;border-bottom:1px solid #3a2c12}
.hero{display:grid;grid-template-columns:1.6fr 1fr;gap:2rem;align-items:center;
padding:2.4rem 0 1.4rem}
.kicker{color:var(--acc);font-weight:700;font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.6rem}
.hero .hot{color:var(--hot)}
.lead{font-size:1.08rem;color:#cdd6e0;max-width:60ch}
.hero-cta{display:flex;gap:.7rem;flex-wrap:wrap;margin-top:1.3rem}
.btn{display:inline-block;background:linear-gradient(90deg,var(--acc),#25c98f);color:#04120c;
font-weight:700;padding:.7rem 1.2rem;border-radius:10px}
.btn:hover{text-decoration:none;filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--tx);box-shadow:inset 0 0 0 1px var(--line)}
.hero-facts{display:grid;gap:.7rem}
.fact{background:var(--panel);border-radius:12px;padding:.9rem 1rem}
.fact b{display:block;font-size:1.5rem;color:var(--warn);letter-spacing:-.01em}
.fact span{font-size:.85rem;color:var(--mut)}
.live-wrap{background:var(--bg2);border-radius:18px;padding:1.2rem;margin:1.2rem 0}
.live-head{display:flex;align-items:center;justify-content:space-between;gap:1rem}
.live-badge{font-weight:800;font-size:.8rem;padding:.2rem .6rem;border-radius:20px}
.live-badge.on{color:#04120c;background:var(--acc)}
.live-badge.off{color:#ffb3b8;background:#2a1418}
.live-stage{position:relative;margin-top:.7rem;border-radius:14px;overflow:hidden;
background:#000;aspect-ratio:16/9}
.live-stage img{width:100%;height:100%;object-fit:contain;display:block}
.live-offline{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
color:var(--mut);background:#05080d}
.ticker{display:flex;gap:.5rem;overflow:hidden;margin:.7rem 0;flex-wrap:wrap}
.tick{background:var(--panel);border-radius:8px;padding:.25rem .6rem;font-size:.82rem;color:var(--mut)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:.8rem;margin-top:.6rem}
.stat{background:var(--panel);border-radius:12px;padding:1rem;text-align:center}
.stat .big{font-size:1.9rem;font-weight:800;letter-spacing:-.02em;
background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.stat .lbl{font-size:.82rem;margin-top:.2rem}.stat .sub{font-size:.72rem;color:var(--mut);min-height:1em}
.verify{margin:2rem 0}
.events{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem;margin-top:1rem}
.ev{margin:0;background:var(--panel);border-radius:14px;overflow:hidden}
.ev img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000}
.ev figcaption{padding:.8rem}
.ev-desc{font-size:.82rem;color:var(--mut);margin-bottom:.5rem;min-height:2.4em}
.ev-q{font-weight:700;margin-bottom:.5rem}
.ev-actions{display:flex;gap:.5rem}
.vbtn{flex:1;border:0;border-radius:9px;padding:.55rem;font-weight:700;cursor:pointer;font-size:.85rem}
.vbtn.confirm{background:#22301f;color:#7fe08a}.vbtn.refute{background:#2f1c1f;color:#ff9aa2}
.vbtn.mine{outline:2px solid var(--acc)}
.vbtn:disabled{opacity:.55;cursor:default}
.ev-tally{font-size:.78rem;color:var(--mut);margin-top:.5rem}
.epi-badge{background:#2f1216;color:#ff8a92;font-weight:800;font-size:.72rem;padding:.15rem .55rem;border-radius:20px;margin-right:.4rem;animation:pulse 1.2s infinite}
@keyframes pulse{50%{opacity:.4}}
.pipe3{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:.8rem;margin:1rem 0}
.pipe3>div{background:var(--panel);border-radius:12px;padding:.9rem}
.pipe3 b{color:var(--acc);display:block;margin-bottom:.3rem;font-size:.92rem}
.pipe3 span{font-size:.85rem;color:#cdd6e0}
.tabs-row{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
.tab{background:var(--panel);color:var(--mut);border:0;border-radius:20px;padding:.4rem .9rem;font-size:.85rem;cursor:pointer;font-weight:600}
.tab.cur{background:var(--acc);color:#04120c}
.ev-media{position:relative;cursor:pointer;background:#000;aspect-ratio:16/9}
.ev-media video,.ev-media img{width:100%;height:100%;object-fit:cover;display:block}
.ev-media .play{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:2.2rem;color:#fff;text-shadow:0 2px 8px #000;opacity:.85}
.ev-top{font-size:.76rem;color:var(--mut);margin-bottom:.4rem}
.ev-ai{font-size:.86rem;color:#dfe7ef;margin-bottom:.5rem;min-height:2.4em}
.badge{font-size:.68rem;font-weight:800;padding:.08rem .4rem;border-radius:6px;white-space:nowrap}
.badge.viol{background:#3a1418;color:#ff8a92}.badge.ok{background:#123021;color:#7fe0a0}
.badge.unc{background:#2f2a12;color:#ffd97a}.badge.wait{background:#1c2432;color:#8b97a7}
.charts{margin:2rem 0}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
.chart-card{background:var(--panel);border-radius:14px;padding:1rem}
.chart-card h3{font-size:.95rem;margin:.2rem 0 .6rem}
.chart{height:150px}.chart .legend{display:flex;gap:.9rem;flex-wrap:wrap;font-size:.72rem;color:var(--mut);margin-top:.3rem}
.chart .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:.25rem;vertical-align:middle}
.dl{margin-top:1rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;color:var(--mut);font-size:.9rem}
.research{background:var(--bg2);border-radius:18px;padding:1.4rem;margin:2rem 0}
.lightbox{position:fixed;inset:0;z-index:100;background:rgba(4,7,12,.96);display:flex;align-items:center;justify-content:center;padding:1rem}
.lb-body{max-width:1200px;width:100%;display:grid;gap:.8rem}
.lb-stage{background:#000;border-radius:12px;overflow:hidden;max-height:70vh}
.lb-stage video,.lb-stage img{width:100%;max-height:70vh;object-fit:contain;display:block}
.lb-info{color:#dfe7ef;font-size:.9rem}
.lb-close,.lb-prev,.lb-next{position:absolute;background:rgba(20,28,40,.8);color:#fff;border:0;border-radius:50%;width:44px;height:44px;font-size:1.4rem;cursor:pointer}
.lb-close{top:1rem;right:1rem}.lb-prev{left:1rem;top:50%}.lb-next{right:1rem;top:50%}
.hooks{margin:2.4rem 0}
.hook-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}
.hook{background:linear-gradient(160deg,var(--panel),var(--bg2));border-radius:14px;padding:1.1rem}
.hook b{display:block;font-size:1.7rem;color:var(--warn);letter-spacing:-.02em;margin-bottom:.3rem}
.hook p{font-size:.9rem;color:#cdd6e0;margin:0}
.hook .src{display:block;font-size:.72rem;color:var(--mut);margin-top:.5rem}
.cols3{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem}
.cols3>div{background:var(--panel);border-radius:14px;padding:1.1rem}
.cols3 ul{margin:.4rem 0 0;padding-left:1.1rem}.cols3 li{margin:.3rem 0;font-size:.92rem}
.note{background:var(--panel2);border-left:3px solid var(--acc);border-radius:10px;padding:.8rem 1rem;color:#cdd6e0}
.pipeline{padding-left:1.2rem}.pipeline li{margin:.5rem 0}
code{background:var(--panel);padding:.1rem .4rem;border-radius:5px;font-size:.9em}
.tbl{width:100%;border-collapse:collapse;margin:1rem 0}
.tbl th,.tbl td{text-align:left;padding:.6rem .7rem;border-bottom:1px solid var(--line)}
.tbl th{color:var(--mut);font-weight:600;font-size:.85rem}
.contact{background:var(--bg2);border-radius:18px;padding:1.4rem;margin:2rem 0}
form{display:grid;grid-template-columns:1fr 1fr;gap:.8rem;max-width:680px;margin-top:1rem}
label{display:grid;gap:.3rem;font-size:.85rem;color:var(--mut)}
label.wide{grid-column:1/-1}
input,select,textarea{background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:.6rem;color:var(--tx);font:inherit;width:100%}
input:focus,select:focus,textarea:focus{outline:2px solid var(--acc);border-color:transparent}
.hp{position:absolute;left:-9999px}
.consent{grid-template-columns:auto 1fr;align-items:start;color:var(--mut);font-size:.82rem}
.consent input{width:auto;margin-top:.2rem}
button[type=submit]{grid-column:1/-1;background:linear-gradient(90deg,var(--acc),#25c98f);
color:#04120c;font-weight:800;border:0;border-radius:10px;padding:.75rem;cursor:pointer;font-size:1rem}
button[type=submit]:disabled{opacity:.6}
.ok{color:var(--acc)}.err{color:var(--hot)}
footer{border-top:1px solid var(--line);margin-top:3rem;padding:1.6rem 1.4rem;color:var(--mut);font-size:.85rem}
.foot-grid{max-width:1160px;margin:0 auto;display:grid;grid-template-columns:2fr 1fr;gap:1.4rem}
.fine{max-width:1160px;margin:1.2rem auto 0;font-size:.78rem}
@media(max-width:820px){.hero{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}
form{grid-template-columns:1fr}.foot-grid{grid-template-columns:1fr}.nav nav{display:none}}
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

> Live, privacy-first pedestrian-crossing safety demonstrator (Poland). A real public
> crossing camera is analysed in real time by a computer-vision model that counts
> pedestrians and vehicles and flags potential conflicts; humans confirm/refute each
> flagged event, and true accuracy is computed from their votes. Faces and licence
> plates are blurred at ingest; frames are not stored; no biometrics. It is NOT
> enforcement — it imposes nothing and keeps no register. Author: Andrii Shramko
> (computer vision / VR / 3D specialist, Poland).

Facts:
- Live camera + YOLOX (Apache-2.0) detector on CPU; ByteTrack/centroid tracking.
- Faces and plates pixelated before display or storage; no face embeddings; ephemeral track IDs.
- Disk holds counters + blurred event snapshots only; no personal data in the output.
- Events are candidates, verified by the public; the shown accuracy is human-derived.
- Honest limits: single uncalibrated camera => many false positives on "did not yield";
  small objects, night, rain reduce accuracy. It is a screening tool, not a measurement instrument.
- Polish context: only ~2% of drivers stop for a pedestrian at the crossing; 458 pedestrians
  killed in 2023 (131 on marked crossings); a fatality costs Poland PLN 2.57M; the 2021 law cut
  crossing accidents by 23.6%. Vision Zero: -50% deaths by 2030.

Pages: %BASE%/ (PL live), %BASE%/en/ (EN live), /how-it-works.html, /accuracy.html,
/resources.html, /compliance.html, /contact.html

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
            if entry == "assets":
                continue
            p = os.path.join(PUB, entry)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(os.path.join(PUB, "assets"), exist_ok=True)
    os.makedirs(os.path.join(PUB, "en"), exist_ok=True)
    gen = {"index": page_index, "how-it-works": page_how, "accuracy": page_accuracy,
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
    for name, content in (("robots.txt", ROBOTS), ("llms.txt", LLMS), ("sitemap.xml", sitemap())):
        with open(os.path.join(PUB, name), "w", encoding="utf-8") as f:
            f.write(content)
    # drop stale synthetic-dashboard assets if present
    for stale in ("dashboard.js", "chart.umd.min.js"):
        fp = os.path.join(PUB, "assets", stale)
        if os.path.exists(fp):
            os.remove(fp)
    if os.path.isdir(os.path.join(PUB, "data")):
        shutil.rmtree(os.path.join(PUB, "data"))
    admin_src = os.path.join(ROOT, "admin.html")
    if os.path.exists(admin_src):
        shutil.copy(admin_src, os.path.join(PUB, "admin.html"))
    print(f"built {n} pages + assets into {PUB}")


if __name__ == "__main__":
    main()
