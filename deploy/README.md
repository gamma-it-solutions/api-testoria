# Testoria deploy — host nginx edge

The public edge is **host-level nginx + system certbot**. The API and all stateful
services (Postgres, Redis, Centrifugo, MinIO) run in Docker and are published on
`127.0.0.1` only; host nginx is the sole public listener on `:80`/`:443`.

```
Internet ──▶ host nginx (:80/:443, system service)
                ├─ testoria.gammait.net   ──▶ /var/www/testoria/current   (SPA files on disk; web-testoria)
                ├─ api.testoria.gammait.net ─▶ 127.0.0.1:8000             (api container)
                └─ s3.testoria.gammait.net ─▶ 127.0.0.1:9000             (minio container)
```

Config ownership:
- `web-testoria/deploy/web.vhost.conf` → `/etc/nginx/sites-available/web.vhost.conf`
- `api-testoria/deploy/api.vhost.conf` → `/etc/nginx/sites-available/api.vhost.conf`
- `api-testoria/deploy/nginx-maps.conf` → `/etc/nginx/conf.d/00-testoria-maps.conf`

Each repo's CI copies its own file(s) and runs `sudo nginx -t && sudo systemctl reload nginx`.

---

## One-time host setup

```bash
# 1. Packages
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx rsync

#    Docker + the Compose *v2 plugin*. CD calls `docker compose` (subcommand,
#    not the legacy `docker-compose` binary) — without the plugin every compose
#    step fails with `unknown flag: --env-file` against the root docker usage.
#    On the Ubuntu-packaged Docker the plugin is `docker-compose-v2` instead.
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # log out/in for this to take effect
docker compose version            # must print v2.x before running CD

# 2. SPA web root
sudo mkdir -p /var/www/testoria/releases /var/www/certbot
sudo chown -R "$USER":www-data /var/www/testoria

# 3. Passwordless sudo for the deploy user (CI runs these non-interactively).
#    Adjust the username; keep this scoped.
sudo tee /etc/sudoers.d/testoria-deploy >/dev/null <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/sbin/nginx, /bin/systemctl reload nginx, \
  /usr/bin/install, /bin/ln, /usr/bin/rsync, /bin/mkdir, /bin/rm, /usr/bin/xargs
EOF
sudo visudo -c

#    (Certs are issued during cutover below — port 80 must be free first.)
```

## Cutover from the old dockerized edge (do once, in a maintenance window)

Order matters: the old dockerized proxy holds `:80`/`:443`, and the SSL vhosts
can't pass `nginx -t` until their certs exist. So: free the ports → bring host
nginx up HTTP-only → issue certs → enable the SSL vhosts → start the api stack.

```bash
# a. Stop the old containerized edge so it frees :80/:443 (old web repo stack).
cd ~/testoria && docker compose -f docker-compose.prod.yml down

# b. Start host nginx with ONLY the default HTTP site (no testoria vhosts enabled
#    yet — they reference certs that don't exist, which would fail `nginx -t`).
sudo systemctl enable --now nginx
sudo nginx -t && sudo systemctl reload nginx

# c. Issue per-app certs now that :80 is free and host nginx answers HTTP-01.
#    (DNS for all three names must already resolve to this host.)
sudo certbot certonly --nginx -d testoria.gammait.net
sudo certbot certonly --nginx -d api.testoria.gammait.net -d s3.testoria.gammait.net
echo 'deploy_hook = systemctl reload nginx' | sudo tee -a /etc/letsencrypt/cli.ini >/dev/null
sudo certbot renew --dry-run

# d. Install + enable the real vhosts (now the certs they reference exist).
#    The api vhost/maps come from this repo; the web vhost is shipped by the web
#    CI run into ~/web-staging/<sha>/ (or copy from a web-testoria checkout).
sudo install -m 0644 deploy/nginx-maps.conf /etc/nginx/conf.d/00-testoria-maps.conf
sudo install -m 0644 deploy/api.vhost.conf  /etc/nginx/sites-available/api.vhost.conf
sudo install -m 0644 ~/web-staging/*/deploy/web.vhost.conf /etc/nginx/sites-available/web.vhost.conf
sudo ln -sfn /etc/nginx/sites-available/api.vhost.conf /etc/nginx/sites-enabled/api.vhost.conf
sudo ln -sfn /etc/nginx/sites-available/web.vhost.conf /etc/nginx/sites-enabled/web.vhost.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# e. Bring up the api stack on loopback ports (nothing published publicly).
cd ~/api-testoria
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d

# f. Smoke test.
curl -fsS https://testoria.gammait.net/health
curl -fsS https://api.testoria.gammait.net/api/v1/health
# fetch one attachment URL → expect 200, not 403 SignatureDoesNotMatch
```

> First-time chicken-and-egg: the web `dist/` and `web.vhost.conf` reach the host
> via a web `Pipeline` run (step d reads `~/web-staging/<sha>/`). Either run the
> web workflow once before cutover, or copy `dist/` to `/var/www/testoria/current`
> and the vhost from a local `web-testoria` checkout by hand for the first deploy.

## Notes

- **Bootstrap the host BEFORE triggering the deploy workflows.** Both CI
  pipelines assume host nginx already exists — they `sudo install` the vhost and
  `systemctl reload nginx`. If you push and run them on a host that hasn't been
  set up, the api stack detaches from the old proxy network while no new edge
  exists yet → the public API/S3 paths 502 until you finish the host setup. Do
  the one-time setup + cutover first, then let CI take over.
- **`http2` directive vs. nginx version.** The vhosts use
  `listen 443 ssl http2;` (the listen-parameter form), which works on the nginx
  shipped by Ubuntu apt (< 1.25.1). The newer standalone `http2 on;` directive
  is **only** valid on nginx ≥ 1.25.1 and errors with
  `unknown directive "http2"` on older builds — check `nginx -v` before changing
  it.
- **MinIO Host header**: `api.vhost.conf` passes `Host $host` unchanged for `s3.*`.
  Rewriting it breaks AWS SigV4 (`403 SignatureDoesNotMatch`). Do not "tidy" it.
- **Centrifugo realtime** is not yet exposed through the edge (it has no host
  port and no public server block). Wiring it is a separate follow-up.
- The old `testoria-proxy` docker network and the `resolver 127.0.0.11` hack are
  gone — host nginx resolves `127.0.0.1` directly and never blocks on a
  restarting container.
