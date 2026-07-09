# Kamery — jak dodawać i co jest wspierane

Kamery są zarządzane z panelu **`/admin.html`** (wymaga `ADMIN_TOKEN`) i zapisywane w
`/data/cameras.json`. Zmiany wchodzą na żywo w ciągu ~5 s (worker przeładowuje konfigurację).
Kamera „aktywna” jest pokazywana wszystkim na stronie głównej; pozostałe tworzą **pulę
failover** (gdy aktywna padnie, system przełącza na następną żywą — każda kamera ma **własną
statystykę** i własny wielokąt strefy).

## Parametry kamery

| Pole | Znaczenie |
|------|-----------|
| `id` | krótki identyfikator (np. `sch`), używany w nazwach plików/statystyk |
| `label` | etykieta widoczna publicznie (miejsce + operator) |
| `url` | adres strumienia (patrz „Typy źródeł”) |
| `referer` | nagłówek Referer, jeśli provider go wymaga (np. LanTech: `https://lantech.com.pl/`) |
| `poly` | wielokąt strefy przejścia — 4–6 punktów `[x,y]` jako **ułamki 0..1** szerokości/wysokości |
| `m_per_px_fullw` | skala: metrów na 1 piksel przy pełnej szerokości kadru (do prędkości; ~0.05–0.12) |

## Typy źródeł (co działa)

1. **HLS `.m3u8` (ZALECANE)** — bezpośredni link do playlisty, np.
   `https://host/hls/<cam>/index.m3u8`. Stabilne, działa z serwerowni. Tak podłączony jest
   LanTech LiveSzczecin. Jak znaleźć link: otwórz stronę kamery → DevTools → Network → filtr
   `m3u8` → skopiuj URL playlisty (i sprawdź, czy leci seria segmentów `.ts`). Jeśli provider
   wymaga Referer — wpisz go w polu `referer`.
2. **MJPEG (`multipart/x-mixed-replace`)** — bezpośredni URL kamery IP. Działa od ręki.
3. **RTSP** — jeśli chcesz kamerę RTSP, podaj `rtsp://…`; OpenCV/FFmpeg zwykle sobie radzi
   (dla kamer za NAT potrzebny publiczny relay). Dla stabilności preferuj HLS.
4. **YouTube (`watch?v=…` lub kanał `/live`)** — **UWAGA:** YouTube blokuje adresy IP
   serwerowni (bot-check), więc na Hetznerze **zwykle NIE działa** bez cookies/proxy. Nadaje
   się do testów z łącza domowego. Dlatego produkcyjnie używamy bezpośredniego HLS.

**Odrzucamy:** snapshoty `.jpg` odświeżane co N sekund (za mało klatek do analizy ruchu),
panoramy gdzie pieszy ma <15 px, oraz `insecam`/niezabezpieczone cudze kamery.

## Dobór strefy `poly` i skali

- `poly` obrysowuje **pasy przejścia** (zebrę) plus wąski pas najazdu, na którym auto mija
  pieszego. Punkty podawaj zgodnie z ruchem wskazówek zegara.
- `m_per_px_fullw`: zmierz coś o znanej długości (szerokość pasa PL ~3,5 m, długość zebry).
  Prędkości są **orientacyjne** (monokularne) — do statystyki, nie do mandatów.
- Po dodaniu kamery AI (Gemini) **raz** opisze scenę (pasy, sygnalizacja, kierunki, pułapki)
  i zapisze do `/data/scenes/scene_<id>.json`; ten opis poprawia interpretację zdarzeń
  (np. „auto stało na czerwonym” = fałszywy alarm).

## Failover i niezawodność

- Kolejność prób: `active` → reszta listy. Padnięcie strumienia → automatyczne przełączenie
  na następną żywą kamerę; powrót po jej odzyskaniu.
- Panel `/admin.html` pokazuje, **które kamery często padały** (liczba down/failover/up +
  ostatnie padnięcie) — na tej podstawie dobierasz backupy.
- Zdarzenia i statystyki są **per-kamera**, więc przełączenie nie miesza danych różnych przejść.
