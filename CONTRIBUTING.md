# Contributing

Thanks for your interest! Ground rules:

1. **Only anonymous / synthetic data.** Never submit real footage, real
   frames, plates, faces, or anything derived from identifiable people.
   PRs containing such material will be closed.
2. **No copyleft dependencies.** The served artifact must stay 0-AGPL/GPL
   (`pip-licenses` gate). Ultralytics YOLO is explicitly off-limits.
3. **Privacy invariants are load-bearing.** Changes must keep the test suite
   green, including `test_privacy.py` (no disk-writing image calls) and the
   failover fact-test.
4. Keep the honesty features intact: coverage normalization, no-data gaps,
   framing-based metric degradation.

By contributing you agree to license your work under Apache-2.0.
