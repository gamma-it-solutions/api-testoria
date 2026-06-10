# Email — Gmail SMTP + durable outbox (plan 048)

Transactional email (welcome set-password invites, password resets) is sent over
**Gmail SMTP + an App Password** via `aiosmtplib`, delivered through a **durable
DB-backed outbox** drained by an in-process worker.

## Components

| Piece | File |
|-------|------|
| Async SMTP sender (STARTTLS, reusable connection, no-op when disabled) | `app/core/email.py` |
| Jinja2 HTML + plaintext templates | `app/templates/email/` |
| Outbox model | `app/models/email_outbox.py` |
| Outbox service (enqueue / claim / mark) | `app/services/email_outbox_service.py` |
| Drain worker (lifespan-managed loop) | `app/core/email_worker.py` |
| Single-use Redis tokens | `app/services/password_token_service.py` |
| Mint token + enqueue message | `app/services/email_service.py` |

## Gmail App Password setup

1. The sending account must have 2-Step Verification enabled.
2. Create an App Password (Google Account → Security → App passwords). It is a
   16-character string; store it **without spaces**.
3. Put it in `.env.prod` (gitignored) as `EMAIL_SMTP_PASSWORD`, set
   `EMAIL_SMTP_USER` to the sending address, `EMAIL_FROM` to the From header.
4. For deliverability, the sending domain (`futurefertility.com`) should have
   SPF + DKIM configured (Workspace).

## Enabling / disabling

`EMAIL_ENABLED` (default **false**) gates the actual SMTP send:
- **false** — the outbox still queues rows and the worker still drains them, but
  `EmailSender.send` is a logged **no-op**. Dev/test never hit Gmail while the
  whole outbox path is still exercised.
- **true** — the worker opens a real STARTTLS connection and sends.

Flip to `true` in prod only after `EMAIL_SMTP_USER`/`EMAIL_SMTP_PASSWORD` are set.

## Tuning (config / env)

| Setting | Default | Meaning |
|---------|---------|---------|
| `EMAIL_OUTBOX_POLL_SECONDS` | 10 | Drain loop poll interval |
| `EMAIL_OUTBOX_BATCH_SIZE` | 50 | Rows claimed per cycle |
| `EMAIL_SEND_PACE_MS` | 200 | Delay between sends on one connection (Gmail-friendly) |
| `EMAIL_MAX_ATTEMPTS` | 5 | Failures before a row → `failed` |
| `EMAIL_INVITE_TOKEN_TTL_SECONDS` | 86400 | Welcome set-password token TTL (24h) |
| `EMAIL_RESET_TOKEN_TTL_SECONDS` | 3600 | Reset token TTL (1h) |
| `FRONTEND_BASE_URL` | — | Base for `/set-password` and `/reset-password` links |

## How draining works

```
enqueue (pending, next_attempt_at=now)
  → worker claim_batch (→ sending, FOR UPDATE SKIP LOCKED, commit)
    → send ok            → sent
    → send fails, n<max  → pending, attempts++, next_attempt_at = now + backoff(n)
    → send fails, n=max  → failed (last_error kept)
```

- The claim is committed *before* sending, so concurrent workers / API replicas
  never double-send (`FOR UPDATE SKIP LOCKED` + the `sending` state).
- Backoff is exponential: 60s, 120s, 240s, … capped at 1h.
- On startup the worker calls `requeue_orphaned_sending` to reset any rows a
  previous run left mid-send (`sending`) back to `pending`.

## Triage

**Rows stuck `pending` (not draining):**
- Confirm the API process is up — the worker runs in the FastAPI lifespan
  (`app/main.py`). If the API is down, nothing drains.
- Check `next_attempt_at` — a backed-off row is *waiting*, not stuck.
- `SELECT count(*), min(created_at) FROM email_outbox WHERE status='pending';`
  A growing count / old `min(created_at)` means the worker isn't keeping up or
  isn't running. Alert when the oldest pending age exceeds TTL/2 (links could
  expire before delivery).

**Rows in `failed`:**
- `SELECT id, to_email, last_error, attempts FROM email_outbox WHERE status='failed';`
- `last_error` holds the SMTP error. Common causes: bad App Password
  (`535`), Gmail rate limit (`421`/`454`), daily cap (~500/day consumer,
  ~2,000/day Workspace).
- There is no replay endpoint yet (tech debt). To retry manually:
  `UPDATE email_outbox SET status='pending', attempts=0, next_attempt_at=now() WHERE id=…;`

**Rows stuck `sending`:**
- Only happens if the worker crashed mid-send; the next startup requeues them.
  To force a requeue without a restart, run the same `UPDATE … SET status='pending'`.

## Scale-out

The in-process loop is sufficient at current volume. Above Gmail's daily caps,
move to a transactional provider (SES/SendGrid) and/or a Celery-based drain
worker (broker already configured) — both tracked in `tech-debt.md`.
