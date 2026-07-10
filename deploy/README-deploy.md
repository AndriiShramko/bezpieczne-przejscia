# Deploy runbook — patrol.flyreelstudio.eu (OPERATOR GATES APPLY)

Target (default per spec): the virtualproduction.business server (neighbour:
Teleportour, Django+Vue). **The stack is additive and must not touch the
neighbour.** Safer alternative: any separate node / Cloudflare Pages.

## Operator-only steps (the agent must NOT do these autonomously)
1. Provide SSH endpoint + **non-root deploy user** scoped to this stack.
   (The Hetzner API token is out of agent scope; read-only list/describe max.)
2. **Independent before-snapshot of the neighbour** (uptime, cert fingerprint) —
   without it the deploy is a hard STOP.
3. DNS: A-record `patrol.flyreelstudio.eu` -> target IP.
4. Reverse proxy / TLS: additive vhost on the existing proxy (own --cert-name,
   ACME staging dry-run first, `reload` not `restart`) OR separate proxy /
   Cloudflare so the neighbour's cert is out of the blast radius.
5. First lead-form secret: copy `config.env` (from the vault secrets note)
   via scp, chmod 600.

## Agent-allowed steps (after operator provides access)
```bash
# pre-flight headroom (abort if <2GB free disk or <300MB free RAM)
df -h / && free -m && docker system df

mkdir -p /home/<deploy-user>/patrol && cd /home/<deploy-user>/patrol
# rsync/scp the repo content (site/public, form-proxy, deploy)
docker compose -f deploy/docker-compose.yml up -d --build

# verify own stack
docker ps --filter name=patrol
curl -s -o /dev/null -w "%{http_code}" http://patrol-web/  # via proxy network

# verify neighbour is untouched (compare with operator's before-snapshot)
docker ps  # all neighbour containers Up, uptime NOT reset
```

## Post-deploy checks (spec-verify D/E)
- `dig +short patrol.flyreelstudio.eu` -> target IP
- `curl -sI https://patrol.flyreelstudio.eu` -> 200, valid CA cert
- lead form E2E -> message arrives in @leadformfromallsitesbot
- neighbour cert fingerprint + container uptimes before == after
- multi-day disk trend + LE rate-limit monitor
