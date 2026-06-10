# Execution Plan: Move the edge proxy to host nginx, each app owns its own vhost

**Date**: 2026-06-01
**Author**: gabriel.arapan
**Status**: In Progress — all in-repo changes landed (vhosts, compose, CI, docs); host setup + cutover pending on EC2 (see `deploy/README.md`)

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.
>
> **Cross-repo + host plan.** This change spans `api-testoria`, `web-testoria`, and host (OS-level)
> configuration. This file is the authoritative copy; a short pointer should be added to
> `web-testoria/docs/04-execution/exec-plans/active/` referencing it. Tasks are tagged
> **[host]**, **[api]**, or **[web]**.

---

## Goal

Run the edge reverse proxy and TLS as **host-level nginx + system certbot** (not in Docker), with each app owning its own vhost file, while the api and all stateful infra stay in Docker — removing the docker-network ownership, resolver hack, bootstrap deadlock, and webroot-certbot complexity entirely.

---

## Context

The edge proxy currently lives inside `web-testoria` as a **dockerized** nginx + certbot:

- `web-testoria/proxy/nginx.conf` terminates TLS and routes `testoria.*`→`web:80`, `api.*`→`api:8000`, `s3.*`→`minio:9000`.
- `web-testoria/docker-compose.prod.yml` runs `nginx-proxy` + `certbot` **and creates** the `testoria-proxy` network; `api-testoria` joins it as `external: true`.

Every pain point this causes is **Docker-specific**, not nginx-specific:

1. **Network ownership coupling / bootstrap deadlock** — web creates the network, api references it as external, so api can't come up unless web deployed first.
2. **`resolver 127.0.0.11 valid=10s` hack** in `proxy/nginx.conf` — needed only because a container upstream can momentarily vanish and otherwise refuses nginx startup.
3. **Dockerized certbot webroot dance** — manual `--expand` SAN edits and a manual `nginx -s reload` cron.

Running the edge on the host eliminates all three: standard `sites-enabled` config, nginx → `127.0.0.1:<port>` (no docker network, no resolver hack), and `certbot` with the system timer (auto-renew + reload). Docker is kept for exactly what it is good at — the api process and the stateful services (Postgres, Redis, MinIO, Centrifugo).

Two correctness facts the host vhosts must preserve (verified against the current `proxy/nginx.conf`):

- **MinIO / S3** uses path-style + AWS SigV4, which signs the `Host` header. The s3 vhost **must** keep `proxy_set_header Host $host;` (any rewrite → `403 SignatureDoesNotMatch`), plus `client_max_body_size 0;`, `proxy_buffering off;`, `proxy_request_buffering off;`, `proxy_http_version 1.1;`, long read/send timeouts.
- The current API vhost is **missing WebSocket `Upgrade`/`Connection` headers**, so Centrifugo realtime is not reachable through the edge today. Exposing it is a follow-up (centrifugo is currently only on the compose `internal` network), not part of this plan.

Related decision recorded in `docs/08-decisions/changelog.md`.

---

## Scope

### In scope
- **[host]** Install and own nginx + certbot at the OS level on the prod host. nginx becomes the only thing binding `:80`/`:443`.
- Each app versions its own complete vhost file in its repo and the deploy step copies it to `/etc/nginx/sites-available/` (symlinked into `sites-enabled/`):
  - **[web]** `web-testoria/deploy/web.vhost.conf` — `testoria.gammait.net`, **serves the SPA `dist/` straight from disk** (folds in the gzip/cache/security-header rules currently in `web-testoria/nginx.conf`).
  - **[api]** `api-testoria/deploy/api.vhost.conf` — `api.testoria.gammait.net` → `127.0.0.1:8000` and `s3.testoria.gammait.net` → `127.0.0.1:9000` (with the MinIO requirements above).
- **Per-app TLS certs** (full separation): web obtains/renews a cert for `testoria.*`; api obtains/renews a cert for `api.*` + `s3.*`. certbot `certonly` only issues/renews; the repo vhosts own the `ssl_certificate` directives, so certbot never edits app config.
- Docker changes:
  - **[api]** Expose container ports to host loopback only — `127.0.0.1:8000:8000` (api), `127.0.0.1:9000:9000` (minio). Drop the `proxy` network and the external `testoria-proxy` reference; keep the `internal` network for inter-service traffic.
  - **[web]** Web no longer runs a prod container — CI builds `dist/` and ships it to the host. Remove `nginx-proxy`, `certbot`, the `web` container, and the `testoria-proxy` network from `web-testoria/docker-compose.prod.yml` (file may be deleted if nothing else remains).

### Out of scope
- Traefik / label-based routing.
- De-Dockerizing the api or any stateful service (Postgres/Redis/MinIO/Centrifugo stay in Docker).
- Exposing Centrifugo WebSocket through the edge (separate follow-up).
- Splitting MinIO root credentials into a scoped S3 user (tracked separately in tech-debt).

---

## Technical approach

### Target layout

```
HOST (EC2)
  /etc/nginx/sites-available/{web,api}.vhost.conf   ← symlinked into sites-enabled/ (copied from repos on deploy)
  /etc/letsencrypt/live/...                          ← per-app certs, system certbot timer renews
  /var/www/testoria/current/                         ← web dist/ (rsynced by CI)  → served directly by nginx
  127.0.0.1:8000  → api container
  127.0.0.1:9000  → minio container

api-testoria/
  deploy/api.vhost.conf                ← api.* → 127.0.0.1:8000  +  s3.* → 127.0.0.1:9000
  docker-compose.prod.yml              ← ports bound to 127.0.0.1; no proxy network; internal net only
  centrifugo/config.json               ← unchanged

web-testoria/
  deploy/web.vhost.conf                ← testoria.* → root /var/www/testoria/current (SPA + gzip/cache/headers)
  nginx.conf                           ← DELETED (rules folded into web.vhost.conf; no inner nginx container)
  proxy/nginx.conf                     ← DELETED
  docker-compose.prod.yml              ← DELETED (no prod container; CI builds dist + rsyncs)
```

### TLS / certbot model

- Issue per app, e.g.
  `certbot certonly --nginx -d testoria.gammait.net` (web)
  `certbot certonly --nginx -d api.testoria.gammait.net -d s3.testoria.gammait.net` (api).
- Repo vhosts contain the full server blocks **including** `ssl_certificate`/`ssl_certificate_key` pointing at `/etc/letsencrypt/live/...`, exactly like today's `proxy/nginx.conf`. certbot only issues/renews — it does **not** rewrite app config, so redeploys never clobber TLS.
- Renewal: the system `certbot.timer` (installed with the package) + `--deploy-hook "systemctl reload nginx"`. Replaces the manual cron.

### Why host nginx (vs. keeping it in Docker)

- Removes docker-network ownership, the resolver hack, the bootstrap ordering, and the webroot dance — all of which exist only because the proxy was containerized.
- Each app's config is a plain file it owns; no bind-mount-from-sibling-repo trick.
- nginx serving the SPA `dist/` directly drops a whole container (the inner web nginx).
- Apps stay in Docker (reproducible, versioned, healthchecked); nginx reaches them on `127.0.0.1`.

### Key decisions

- **Loopback-only port exposure** for api and minio (`127.0.0.1:`), so only host nginx can reach them — nothing is published on the public interface except nginx.
- **Per-app certs** instead of one SAN cert — completes the ownership split (web's TLS is web's, api's is api's).
- **SPA served from disk** rather than a web container — simplest, one fewer moving part; the gzip/cache/security-header rules from `web-testoria/nginx.conf` move into `web.vhost.conf`.
- **certbot `certonly`** (issue only) — keeps full vhost config in the repos and avoids certbot editing app files.

---

## Tasks

### Host setup — [host]
- [ ] Install `nginx`, `certbot`, `python3-certbot-nginx` on the prod host.
- [ ] Create `/var/www/testoria/current/` (or a `releases/ + current` symlink) for the SPA artifact; set ownership for the deploy user.
- [ ] Confirm nginx `http {}` has sane global defaults (gzip, `server_names_hash_bucket_size` if needed); confirm `sites-enabled/*` is included.
- [ ] Issue per-app certs with `certbot certonly --nginx` (web domain; api + s3 domains); enable the `certbot.timer`; set `--deploy-hook "systemctl reload nginx"`.

### Implementation — web
- [ ] Create `web-testoria/deploy/web.vhost.conf`: 80→443 redirect + 443 server for `testoria.gammait.net`, `root /var/www/testoria/current`, SPA `try_files $uri $uri/ /index.html`, plus the gzip/cache/`X-Frame-Options`/etc. rules folded in from `nginx.conf`, and `/health`.
- [ ] Delete `web-testoria/nginx.conf`, `web-testoria/proxy/nginx.conf`, and `web-testoria/proxy/`.
- [ ] Delete (or empty) `web-testoria/docker-compose.prod.yml` — no prod container.
- [ ] Update web CI: after `npm run build`, rsync `dist/` to `/var/www/testoria/<release>/`, flip the `current` symlink, copy `deploy/web.vhost.conf` to the host, `nginx -t && systemctl reload nginx`.
- [ ] Update `web-testoria/Dockerfile` use: the build stage is still used in CI to produce `dist/`; the nginx stage is removed (or the whole Dockerfile dropped if CI builds dist outside Docker).

### Implementation — api
- [ ] Create `api-testoria/deploy/api.vhost.conf`: 80→443 redirect; 443 server `api.testoria.gammait.net` → `proxy_pass http://127.0.0.1:8000`; 443 server `s3.testoria.gammait.net` → `proxy_pass http://127.0.0.1:9000` with `Host $host`, buffering off, `client_max_body_size 0`, long timeouts.
- [ ] Edit `api-testoria/docker-compose.prod.yml`: change `api` from `expose: 8000` to `ports: ["127.0.0.1:8000:8000"]`; add `ports: ["127.0.0.1:9000:9000"]` to `minio`; remove the `proxy` network from `api` and `minio` and delete the external `testoria-proxy` network block; keep `internal`.
- [ ] Move the certbot bootstrap/renew notes out of the old web compose into api docs/runbook (the host now owns renewal).

### Quality check
- [ ] `nginx -t` passes on the host with both vhosts in `sites-enabled/`.
- [ ] HTTP→HTTPS redirect works for all three hostnames; ACME renewal verified with `certbot renew --dry-run`.
- [ ] `https://testoria.*` serves the SPA and deep links (e.g. `/projects/123`) fall back to `index.html`.
- [ ] `https://api.*` reaches the API; `https://s3.*` fetch of an attachment returns 200 (no `403 SignatureDoesNotMatch`).
- [ ] No container port is published on the public interface (`ss -tlnp` shows api/minio on `127.0.0.1` only; nginx on `0.0.0.0:80,443`).
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed (both repos).

### Docs update
- [ ] `docs/08-decisions/changelog.md` **[api + web]** — record the move to host nginx + per-app certs and the rationale.
- [ ] `docs/04-execution/tech-debt.md` **[api + web]** — close items tied to the dockerized proxy (resolver hack, network coupling); add the Centrifugo-WS-through-edge follow-up.
- [ ] Update the deploy-topology description in both repos' architecture docs (edge nginx now host-level; apps on `127.0.0.1`).
- [ ] Add/refresh a host runbook section: install steps, cert issuance, the deploy rsync+reload flow.
- [ ] Add the pointer plan in `web-testoria/docs/04-execution/exec-plans/active/` referencing this file.
- [ ] This plan moved from `active/` to `completed/`.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| MinIO breaks if `Host` is rewritten in the s3 vhost | Medium | Copy the existing s3 server block verbatim; keep `Host $host`; smoke-test an attachment fetch before declaring done |
| SPA cache/security headers lost when dropping the inner nginx | Medium | Explicitly fold every rule from `web-testoria/nginx.conf` into `web.vhost.conf`; diff old vs new response headers |
| Cutover downtime while `:80`/`:443` move from the container to host nginx | Medium | Maintenance window: stop the dockerized proxy, free 80/443, start host nginx with both vhosts already in place and certs pre-issued |
| Container ports accidentally published publicly | Low | Bind to `127.0.0.1:` explicitly; verify with `ss -tlnp` in the quality check |
| Host config drifts from repo (manual edits on the box) | Medium | Repo is source of truth; deploy always copies `deploy/*.vhost.conf` + `nginx -t`; no hand-edits on the host |
| certbot renewal not reloading nginx | Low | Use `--deploy-hook "systemctl reload nginx"`; verify with `certbot renew --dry-run` |

---

## Definition of done

- [ ] Edge proxy + TLS run as host-level nginx + system certbot; no proxy/certbot containers anywhere.
- [ ] `web-testoria` contains no routing/TLS for api or s3 and no prod container; it ships `deploy/web.vhost.conf` + a `dist/` artifact.
- [ ] `api-testoria` owns `deploy/api.vhost.conf`; api and minio publish only on `127.0.0.1`; no `testoria-proxy` network anywhere.
- [ ] Each app owns its own vhost and its own cert; api comes up independently of web.
- [ ] All three hostnames serve over HTTPS; redirect, SPA fallback, API call, and an S3 attachment fetch verified; `certbot renew --dry-run` clean.
- [ ] Docs + changelog + host runbook updated in both repos; web pointer plan created.
