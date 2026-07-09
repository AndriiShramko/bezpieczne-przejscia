# Privacy & legal design

- Minimisation (GDPR Art. 5(1)(c)): disk holds only aggregate counters.
- Faces/plates pixelated at inference; the raw frame exists only transiently
  in RAM; no archive, no streaming.
- Zero biometrics: no embeddings, no templates, no recognition
  (AI Act Art. 5 prohibited practices are structurally avoided).
- No register of offences (GDPR Art. 10): nothing is attributed to persons.
- No automated decisions about individuals (Art. 22).
- Processing moment basis: legitimate interest (Art. 6(1)(f), road safety),
  documented by an LIA + DPIA signed off by a lawyer BEFORE any real frame.
- Source legality gate: client-facing showcases run only on permissioned or
  own cameras; scraping third-party streams violates ToS and is not used for
  showcases.
- Public-sector deployment: the authority's own legal basis (Art. 6(1)(e)),
  supplier as processor under Art. 28.

This document describes engineering intent, not legal advice.
