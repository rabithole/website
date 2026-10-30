# Deployment

**Live site:** https://www.rabithole.com (also answers on `rabithole.com`)

## How a deploy happens

```
git push origin master
        │
        ▼
GitHub Actions: "Deploy to production" (.github/workflows/deploy.yml)
        │  ssh -i <deploy key> $DEPLOY_USER@$DEPLOY_HOST "deploy"
        ▼
VPS: forced command → deploy.sh
        │  git fetch + git reset --hard  (to origin/master)
        │  restart the server.py service
        ▼
nginx (HTTPS) ──▶ server.py on 127.0.0.1:8080
```

- **Only pushes to `master` deploy.** Pushes to other branches do nothing.
  `main` exists on GitHub but is just the original "Initial commit" with a
  LICENSE — the site is entirely on `master`.
- A typical run takes about 10 seconds. Check results under the repo's
  **Actions** tab.
- **Every push to `master` goes live**, including docs-only changes (those just
  restart the server — harmless, but it clears in-memory carts and the login throttle).

## GitHub secrets

Set in GitHub → repo → Settings → Secrets and variables → Actions:

| Secret | What it is |
|---|---|
| `DEPLOY_SSH_KEY` | Private SSH deploy key, **base64-encoded** (the workflow runs `base64 -d`). |
| `DEPLOY_HOST` | VPS hostname or IP. |
| `DEPLOY_USER` | Linux user on the VPS that owns the app. |

## On the server (VPS)

These live on the server, not in this repo:

- **`~/.ssh/authorized_keys`** — the deploy key's entry has a *forced command*,
  so that key can only ever run `deploy.sh`, whatever command the workflow sends.
- **`deploy.sh`** — fetches, hard-resets the checkout to `origin/master`, and
  restarts the app.
- **nginx** — terminates HTTPS for `rabithole.com` / `www.rabithole.com`,
  proxies to `127.0.0.1:8080`, and sets `X-Forwarded-For` (used for the login
  rate limit).
- **Untracked files that survive deploys** (because they're gitignored, `reset
  --hard` leaves them alone): `db/rabithole.db`, `paypal_config.json`,
  `assets/uploads/`.

> Keep a copy of `deploy.sh`, the nginx site config and the service unit
> somewhere outside the server (a private note or a `deploy/` folder without
> secrets) so the server can be rebuilt.

## What a deploy does *not* do

- Does not copy your local database, uploads or PayPal keys. Production data
  only changes through the live site.
- Does not run tests or checks — a syntax error in `server.py` will deploy and
  the site will go down. Run it locally first (see below).
- Does not roll back automatically.

## Before you push

```bash
python -m py_compile server.py manage_users.py   # catches syntax errors
python server.py                                  # click through the site at http://localhost:8080
git status                                        # make sure no db/, uploads or keys are staged
```

## Rolling back

```bash
git revert <bad-commit>      # creates a new commit that undoes it
git push origin master       # deploys the fix
```

Avoid `git push --force` to `master`; the server hard-resets to whatever
`master` points at, so a force-push will deploy immediately.

## Troubleshooting

| Symptom | Check |
|---|---|
| Action fails at "Deploy over SSH" | Secrets still set? Key still in `authorized_keys`? Server reachable? |
| Action green but site shows old code | Did `deploy.sh` restart the service? Check the service status/logs on the VPS. |
| 502 Bad Gateway | `server.py` isn't running (crashed or failed to start). Check logs; often a missing `db/rabithole.db` or a syntax error. |
| Checkout says PayPal not configured | `paypal_config.json` on the server is missing keys. |
| Browser shows a cert/TLS error (`ERR_CERT_DATE_INVALID`, etc.) | The Let's Encrypt cert expired. To fix immediately: SSH in (`ssh root@<VPS IP>`) and run `certbot renew --non-interactive && nginx -t && systemctl reload nginx`, then verify with `certbot certificates`. Certs are issued for 90 days. |

### Cert auto-renewal

This server (AlmaLinux) has no `certbot.timer` — certbot here relies on a cron job, not systemd, for auto-renewal. As of Oct 2026 this cron job exists at `/etc/cron.d/certbot`:

```
0 3,15 * * * root certbot renew --quiet --deploy-hook 'systemctl reload nginx'
```

(runs twice daily; `certbot renew` only actually renews when a cert is within 30 days of expiry). `crond.service` must also be `active`/`enabled` for this to fire — check with `systemctl status crond`. If the cert ever expires again, check this cron job and the crond service first, before assuming a systemd timer exists.
