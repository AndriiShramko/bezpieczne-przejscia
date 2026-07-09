# Security Policy

If you find a vulnerability — especially anything that could leak
non-aggregate data (frames, identities, embeddings) or the lead-form
token path — please report it privately to **zmei116@gmail.com**.
Do not open a public issue for security reports.

Scope notes:
- The pipeline must never persist images; `pipeline/tests/test_privacy.py`
  encodes this invariant.
- The lead-form bot token lives only in server-side env (`deploy/config.env`,
  never in git, never in client JS).
