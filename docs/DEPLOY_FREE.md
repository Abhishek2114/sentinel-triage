# Free deploy: Render + Neon + Upstash

The whole app runs as one container on Render's free plan. The database and
Redis are free hosted services. You need no domain. The public address will
be:

```
https://sentinel-triage.onrender.com
```

If someone already has that name, Render adds a few characters to it.

```mermaid
flowchart LR
    Browser -->|HTTPS + WebSocket| Render["Render web service: API + dashboard + scheduler"]
    Render -->|TLS| Neon[(Neon Postgres + pgvector)]
    Render <-->|TLS pub/sub| Upstash[(Upstash Redis)]
```

**The trade-off for free:**
- **The app sleeps** after 15 minutes without visitors. The next visit wakes
  it, which takes about a minute.
- **It's slow.** The free plan has a tenth of a CPU and 512 MB of memory, so
  the demo takes longer than on a laptop.
- **The database pauses** after a few minutes idle. Neon pauses compute, and
  the first query afterwards takes about a second.
- **750 hours a month.** That covers one service running all month. Past it,
  Render stops free services until the next month.

Celery isn't used here. The API runs detection itself, and only after new
logs arrive, so an idle app never queries the database. That keeps Neon's
free compute hours for when someone is actually using the app.

You'll create three accounts and copy three connection strings. Keep them
somewhere private until step 4.

## 1. Postgres on Neon

1. Sign up at [neon.tech](https://neon.tech). Signing in with GitHub works.
2. Create a project.
   - **Name:** `sentinel-triage`.
   - **Postgres version:** the newest.
   - **Region:** AWS US East (N. Virginia), next to the Render region in
     [`render.yaml`](../render.yaml). The database adds latency to every
     request, so if pages feel slow later, check this first.
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
2. Create a **Redis** database on the free plan, in an AWS US East region.
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

## 4. The app on Render

Everything Render needs is in [`render.yaml`](../render.yaml) at the repo
root, a Render *Blueprint*: it builds the root `Dockerfile` on the free plan
and sets the demo defaults. You only supply the three secrets.

1. Sign up at [render.com](https://render.com) with **GitHub**. That also
   lets Render see your repositories.
2. Click **New → Blueprint**.
3. Connect the `sentinel-triage` repository. If it isn't listed, choose
   **Configure account** and give Render access to it.
4. Render reads `render.yaml` and shows one web service, `sentinel-triage`,
   on the **Free** plan. Give the Blueprint a name (`sentinel-triage` is
   fine).
5. It asks for the three values marked as secrets:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string |
   | `REDIS_URL` | the Upstash `rediss://` URL |
   | `API_KEYS` | `["<your key>"]`, keeping the brackets and quotes |

6. Click **Deploy Blueprint**. Open the service and watch its **Logs**. The
   first build takes around 10 minutes, most of it installing Python
   packages. When the log shows `Uvicorn running on http://0.0.0.0:10000`
   and the service is marked **Live**, it's up.

## 5. Check it

The address is at the top of the service's page in Render.

- Open it. The status in the top bar should read **Live**.
- Press **Run demo**. The pipeline should step through to "Incident #1
  raised", and an alert should slide in.

From a terminal, check the health endpoint:

```bash
curl https://sentinel-triage.onrender.com/api/v1/health
```

It should report `"db":"connected"`. A write without the key must be refused
with `401`:

```bash
curl -i -X POST https://sentinel-triage.onrender.com/api/v1/detect -H 'Content-Type: application/json' -d '{}'
```

## Optional: an LLM for the triage reports

Without one, triage writes rule-based reports. To use Claude, add these in
the service's **Environment** tab:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key |
| `LLM_PROVIDER` | `anthropic` |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` |

A daily cap limits spend: `LLM_MAX_CALLS_PER_DAY` is 100 here. Past the cap,
triage falls back to rule-based reports.

## Sending real logs

Run the shipping agent wherever your logs are, pointing it at the app and
passing your key. The dashboard's **Ship logs** page shows the exact commands
for your address. Detection runs within
`DETECTION_INTERVAL_SECONDS` (default 5 minutes) after logs arrive.

Neon's free plan stores 0.5 GB. The demo replaces its own data on every run,
but real logs add up, so keep an eye on usage if you ship a lot.

## Updating

Render redeploys on its own whenever `main` changes. To deploy by hand, use
**Manual Deploy → Deploy latest commit** on the service page.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Log stops at `alembic upgrade head` with a connection error | `DATABASE_URL` is wrong, or it's the pooled Neon address |
| `prepared statement ... does not exist` in the logs | The pooled Neon address; use the direct one |
| Dashboard works but the status says **Reconnecting** | `REDIS_URL` is wrong or uses `redis://` instead of `rediss://` |
| `RuntimeError: ENVIRONMENT=production requires API_KEYS` | The `API_KEYS` secret is missing or isn't a JSON list |
| First visit takes about a minute | The app was asleep; later visits are fast |
| Service restarts with "Ran out of memory" | Something heavier than the demo ran, e.g. `POST /detect` naming `lstm_ae`; the free plan has 512 MB |

## Alternative: a Hugging Face Space (paid)

Since mid-2026, Docker Spaces on Hugging Face need a PRO account ($9 a
month); only static Spaces are free. With PRO, the same app runs as a
Space from the two files in [`deploy/huggingface/`](../deploy/huggingface):

1. Create a Space with the **Docker** SDK, **Blank** template, **CPU basic**
   hardware and **Public** visibility.
2. Add `deploy/huggingface/Dockerfile` as the Space's `Dockerfile`, and
   replace the Space's `README.md` with `deploy/huggingface/README.md`,
   keeping the block between the `---` lines.
3. Under **Settings → Variables and secrets**, add `DATABASE_URL`,
   `REDIS_URL` and `API_KEYS` as **secrets**, not variables: on a public
   Space, variables are visible to everyone.

The Space clones `main` when it builds; **Settings → Factory rebuild** picks
up new commits.

## Moving to a paid server later

The same code runs on a VPS with the full stack (Celery, Caddy, local
Postgres): see [DEPLOY.md](DEPLOY.md). Point that setup's database at
Neon, or dump and restore, to keep your data.
