# Free deploy: Hugging Face Spaces + Neon + Upstash

The whole app runs as one container on a free Hugging Face Space. The
database and Redis are free hosted services. You need no card and no domain.
The public address will be:

```
https://<your-hf-username>-sentinel-triage.hf.space
```

```mermaid
flowchart LR
    Browser -->|HTTPS + WebSocket| Space["HF Space: API + dashboard + scheduler"]
    Space -->|TLS| Neon[(Neon Postgres + pgvector)]
    Space <-->|TLS pub/sub| Upstash[(Upstash Redis)]
```

**The trade-off for free:**
- **The Space sleeps** after about two days without visitors. The next visit
  wakes it, which takes a minute or two.
- **The database pauses** after a few minutes idle. Neon pauses compute, and
  the first query afterwards takes about a second.

Celery isn't used here. The API runs detection itself, and only after new
logs arrive, so an idle Space never queries the database. That keeps Neon's
free compute hours for when someone is actually using the app.

You'll create three accounts and copy three connection strings. Keep them
somewhere private until step 4.

## 1. Postgres on Neon

1. Sign up at [neon.tech](https://neon.tech). Signing in with GitHub works.
2. Create a project.
   - **Name:** `sentinel-triage`.
   - **Postgres version:** the newest.
   - **Region:** choose an AWS US East region. The database adds latency to
     every request, so a region near the Space matters. If pages feel slow
     later, check this first.
3. Open **Connect** (or **Connection details**) and turn **connection pooling
   off**. The pooled address goes through PgBouncer, which breaks the
   prepared statements the app's driver uses.
4. Copy the connection string. It looks like this:
   ```
   postgresql://neondb_owner:...@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```
   Paste it unchanged. The app converts `sslmode` into the form its driver
   needs.

You don't need to set anything up in the database. The app creates its tables
and the `vector` extension on first start.

## 2. Redis on Upstash

1. Sign up at [upstash.com](https://upstash.com).
2. Create a **Redis** database on the free plan, with a region near the Neon
   one.
3. Copy the **TLS** connection URL, the one starting with `rediss://` (two
   s's):
   ```
   rediss://default:...@xxxx.upstash.io:6379
   ```

Redis only carries the live incident alerts. That's one long-lived connection
plus one message per incident, far below the free plan's limits.

## 3. An API key

Anyone can browse the demo and press **Run demo**. Everything else, such as
sending logs or resolving incidents, needs a key. Make a long random one.

On macOS or Linux:

```bash
openssl rand -hex 32
```

Without a terminal, any password generator making 40+ characters works. Keep
the key; it's what you'll paste into the dashboard's **API key** box.

## 4. The Space

1. Sign up at [huggingface.co](https://huggingface.co).
2. Create a new Space (**New → Space**).
   - **Space name:** `sentinel-triage`
   - **SDK:** Docker, using the **Blank** template
   - **Hardware:** CPU basic, which is free
   - **Visibility:** Public
3. In the Space's **Files** tab, add the two files from this repo's
   [`deploy/huggingface/`](../deploy/huggingface) folder:
   - **Add file → Create a new file**, name it `Dockerfile`, and paste in
     [`deploy/huggingface/Dockerfile`](../deploy/huggingface/Dockerfile).
   - Edit the existing `README.md` and replace all of it with
     [`deploy/huggingface/README.md`](../deploy/huggingface/README.md). Keep
     the block between the `---` lines: it tells Hugging Face to run Docker
     on port 7860.
4. In **Settings → Variables and secrets**, add these as **secrets**, not
   variables. On a public Space, variables are visible to everyone.

   | Secret | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string |
   | `REDIS_URL` | the Upstash `rediss://` URL |
   | `API_KEYS` | `["<your key>"]`, keeping the brackets and quotes |

5. The Space builds on its own; watch the **Logs** tab. The first build takes
   around 10 minutes, most of it installing Python packages. When the log
   shows `Uvicorn running on http://0.0.0.0:7860`, it's live.

## 5. Check it

Open `https://<your-hf-username>-sentinel-triage.hf.space`. This direct
address gives the dashboard the whole window. The Space page on
huggingface.co shows the same app in a frame.

- The status in the top bar should read **Live**.
- Press **Run demo**. The pipeline should step through to "Incident #1
  raised", and an alert should slide in.

From a terminal, check the health endpoint:

```bash
curl https://<your-hf-username>-sentinel-triage.hf.space/api/v1/health
```

It should report `"db":"connected"`. A write without the key must be refused
with `401`:

```bash
curl -i -X POST https://<your-hf-username>-sentinel-triage.hf.space/api/v1/detect -H 'Content-Type: application/json' -d '{}'
```

## Optional: an LLM for the triage reports

Without one, triage writes rule-based reports. To use Claude, add:

| Kind | Name | Value |
|---|---|---|
| secret | `ANTHROPIC_API_KEY` | your key |
| variable | `LLM_PROVIDER` | `anthropic` |
| variable | `ANTHROPIC_MODEL` | `claude-haiku-5-5` |

A daily cap limits spend: `LLM_MAX_CALLS_PER_DAY` defaults to 100 here. Past
the cap, triage falls back to rule-based reports.

## Sending real logs

Run the shipping agent wherever your logs are, pointing it at the Space and
passing your key. The dashboard's **Ship logs** page shows the exact commands
for your address. Detection runs within
`DETECTION_INTERVAL_SECONDS` (default 5 minutes) after logs arrive.

Neon's free plan stores 0.5 GB. The demo replaces its own data on every run,
but real logs add up, so keep an eye on usage if you ship a lot.

## Updating

The Space clones this repo's `main` branch when it builds. To deploy new
commits, use **Settings → Factory rebuild**. To pin a release instead, change
the Space `Dockerfile`'s `ARG REF=main` line to a tag or full commit SHA.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Build log stops at `alembic upgrade head` with a connection error | `DATABASE_URL` is wrong, or it's the pooled Neon address |
| `prepared statement ... does not exist` in the logs | The pooled Neon address; use the direct one |
| Dashboard works but the status says **Reconnecting** | `REDIS_URL` is wrong or uses `redis://` instead of `rediss://` |
| `RuntimeError: ENVIRONMENT=production requires API_KEYS` | The `API_KEYS` secret is missing or isn't a JSON list |
| First visit takes a minute or two | The Space was asleep; later visits are fast |

## Moving to a paid server later

The same code runs on a VPS with the full stack (Celery, Caddy, local
Postgres): see [DEPLOY.md](DEPLOY.md). Point that setup's database at
Neon, or dump and restore, to keep your data.
