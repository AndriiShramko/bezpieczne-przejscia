#!/usr/bin/env bash
# Static-site release of patrol.flyreelstudio.eu (the live stack is ~/patrol on the Hetzner hub,
# container patrol-web = nginx:alpine with ./dist bind-mounted read-only).
#
#   bash deploy/deploy_static.sh            build -> GA guard -> package -> upload -> backup -> extract -> live check
#   bash deploy/deploy_static.sh --dry-run  build + GA guard + package only (nothing leaves this machine)
#
# What it does NOT touch: nginx.conf, docker-compose.yml, patrol-cv / patrol-form, config.env, cv.env, cv-data.
# Shared hub: other agents deploy other sites — hold the hub deploy lock (/tmp/hub-deploy.lock on the server)
# around a real run and compare `docker ps` + neighbour HTTP codes before/after.
#
# GA guard: the GA4 measurement id is committed in site/build_site.py (GA_ID). A release without it is REFUSED
# here (and by build_site.py itself) — Drony Bydgoszcz lost GA for 8 days to a release built without its id.
#
# Rollback (in place — never `mv` dist, the bind mount keeps the old inode):
#   ssh -p 2222 -i ~/.ssh/fpv_hetzner_key fpv@65.109.11.177 'cd ~/patrol && rsync -a --delete dist.bak-<STAMP>/ dist/'
set -euo pipefail

GA_ID="G-4561KTT50X"
HOST="fpv@65.109.11.177"
SSH_OPTS=(-p 2222 -i "$HOME/.ssh/fpv_hetzner_key" -o ConnectTimeout=20)
SCP_OPTS=(-P 2222 -i "$HOME/.ssh/fpv_hetzner_key" -o ConnectTimeout=20)
LIVE="https://patrol.flyreelstudio.eu"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUB="$ROOT/site/public"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

python "$ROOT/site/build_site.py"

# --- GA guard (two independent checks; any failure = nothing is uploaded) -------------------------
grep -q "GA_ID = \"$GA_ID\"" "$ROOT/site/build_site.py" || { echo "REFUSED: build_site.py GA_ID is not $GA_ID - nothing uploaded" >&2; exit 1; }
python "$ROOT/site/build_site.py" --check-ga "$PUB" || { echo "REFUSED: GA check failed - nothing uploaded" >&2; exit 1; }
missing="$(cd "$PUB" && find . -name '*.html' ! -name admin.html -print0 | xargs -0 grep -L "data-ga=\"$GA_ID\"" || true)"
[ -z "$missing" ] || { echo "REFUSED: $GA_ID missing in: $missing - nothing uploaded" >&2; exit 1; }
grep -q "googletagmanager.com/gtag/js" "$PUB/shramko-analytics.js" || { echo "REFUSED: /shramko-analytics.js missing or broken - nothing uploaded" >&2; exit 1; }
echo "GA guard OK: $GA_ID in every page"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
TGZ="$TMP/dist-$STAMP.tgz"
( cd "$PUB" && tar czf "$TGZ" . )
echo "package: $TGZ ($(du -k "$TGZ" | cut -f1) KB)"
[ "$DRY" = 1 ] && { echo "dry run: nothing uploaded"; exit 0; }

scp "${SCP_OPTS[@]}" "$TGZ" "$HOST:patrol/dist-$STAMP.tgz"
ssh "${SSH_OPTS[@]}" "$HOST" "set -e; cd ~/patrol
  cp -a dist dist.bak-$STAMP && diff -rq dist dist.bak-$STAMP >/dev/null && echo 'backup dist.bak-$STAMP verified'
  tar xzf dist-$STAMP.tgz -C dist          # over the existing dir (same inode for the bind mount)
  rm -f dist-$STAMP.tgz
  grep -q 'data-ga=\"$GA_ID\"' dist/index.html && echo 'extracted, GA id present on the server'"

# --- live check ----------------------------------------------------------------------------------------
for u in / /pl/ /pl/privacy.html /es/privacy.html; do
  curl -fsS "$LIVE$u?nocache=$STAMP" | grep -q "data-ga=\"$GA_ID\"" || { echo "LIVE CHECK FAILED on $u - roll back with dist.bak-$STAMP" >&2; exit 2; }
done
echo "deployed release $STAMP; rollback: cd ~/patrol && rsync -a --delete dist.bak-$STAMP/ dist/"
