# Analytics — patrol.flyreelstudio.eu

GA4 property **559875257** "Bezpieczne Przejścia (patrol)", web stream measurement id **`G-4561KTT50X`**
(committed in `site/build_site.py` → `GA_ID`; public, not a secret).

## Rules (WEB-TECH-BASELINE-2026 §T6)
- **Consent Mode basic.** Nothing is requested from Google before the visitor clicks **Akceptuję / Accept /
  Aceptar / Принять**. Only then `/shramko-analytics.js` injects `gtag.js` and runs `config`, so the page_view
  of the landing page is sent **granted** (`gcs=G101`). Reject (**Odrzucam / Reject / Rechazar / Отклонить**)
  has the same visual weight; nothing is loaded.
- **One page_view per page load** (the automatic one from `config`). No manual page_view, no history hook.
- **Withdraw:** footer link "Ustawienia cookies / Cookie settings / Configuración de cookies / Настройки cookie"
  on every page reopens the banner → Reject: `ga-disable-G-4561KTT50X = true`, `consent update denied`,
  `_ga*` cookies deleted (host + parent domains), choice stored in `localStorage.sa_consent`, no further hits.
- `cookie_domain: location.hostname` (cookies on `.patrol.flyreelstudio.eu`, not shared with other subdomains),
  cookie lifetime 400 days, GA data retention 14 months, Google Signals / ad personalisation off.
- Admin sessions are never tracked (`localStorage.bp_admin` or `admin=` in the URL); `/admin` has no analytics.
- No personal data in any hit: the lead form reports field **names** and a segment code, never values.
- The root `/` auto-redirect (browser language) keeps the external referrer (`sessionStorage.sa_ref` →
  `page_referrer`); localized URLs and deep links are never redirected (T9).

## Events
| Event | Params | Where |
|---|---|---|
| `page_view` | `site_lang` | automatic (`config`) |
| `consent_accept` | — | Accept click |
| `cta_click` | `cta_id`, `cta_location` | `[data-cta]`, `.btn`; stat cards `stat_events`/`stat_accuracy`, charts `chart_speed_hist`/`chart_speeding_tod`/`chart_events_hour` |
| `contact_click` | `method` (email/phone/telegram/whatsapp/messenger), `cta_location` | mailto/tel/… links |
| `lang_switch` | `lang_to` (beacon) | language switcher |
| `share` | `network` (linkedin/facebook/x/copy), `cta_location` (card/lightbox/about…) | share buttons, copy-link |
| `tab_select` | `tab` | event tabs |
| `event_open` | `tab` | event card → fullscreen viewer |
| `event_vote` | `vote` (violation/false_alarm), `tab`, `cta_location` (card/lightbox) | after the server accepted the vote |
| `live_view` | `cam_id` | live section ≥50 % visible while the stream is live (once per page) |
| `live_offline` | `cam_id` | the live view showed OFFLINE (once per page) |
| `video_play` / `video_complete` | `video_id` (`event_clip`) | clip in the viewer (opened by the visitor; loop wrap = complete) |
| `section_view` | `section_id` | hero, live, verify, charts, why, research, about, faq, kontakt (once each) |
| `form_start` | `form_id` (`leadform`) | GA4 Enhanced Measurement |
| `form_step` | `form_id`, `step` (field name) | first change of each lead-form field |
| `form_submit` | `form_id` | submit of the lead form (EM does not send it for this fetch form) |
| `generate_lead` | `form_id`, `lead_type` (municipality/national_body/company/research/other) | only after `/api/lead` returned 2xx |
| `form_error` | `form_id`, `reason` (http/network) | lead submission failed |
| `file_download_click` | `file_ext`, `cta_location` | CSV report |
| `exception` | `description` (≤100 chars, URLs stripped), `fatal:false` | own JS errors, max 5 per page |

All events carry `site_lang`. Custom dimensions registered in the property: `cta_id, cta_location, section_id,
form_id, site_lang, method, lang_to, video_id, file_ext, tab, vote, network`. Params still to register:
`cam_id, step, lead_type, reason`.

## Verify
- Build refuses to finish if any page lacks `data-ga="G-4561KTT50X"`: `python site/build_site.py`
  (or `python site/build_site.py --check-ga site/public`). `deploy/deploy_static.sh` repeats the check before upload.
- Live: `node ga_probe.cjs https://patrol.flyreelstudio.eu/ https://patrol.flyreelstudio.eu/pl/contact.html`
  (vault `03 - Resources/Deployment/analytics/`) → verdict `OK`.
