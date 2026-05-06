# HMIS WhatsApp Analytics Bot

A WhatsApp bot that lets immunization field officers in Bihar / UP ask plain-language questions about HMIS data and receive summarized answers, charts, and downloadable Excel / PowerPoint exports.

Users speak (or type) in English, Hindi, or Hinglish on WhatsApp. The bot extracts the geography, generates a BigQuery SQL query, runs it, summarises the result in plain language, and sends back the answer along with an inline chart and downloadable Excel + PowerPoint files.

---

## Architecture

```
WhatsApp user
   │  (text or voice message)
   ▼
Meta WhatsApp Cloud API
   │  (HTTPS POST webhook)
   ▼
FastAPI app (bot.py)  ─── pipeline ──►  OpenAI / BigQuery
   │                                              │
   ▼                                              ▼
WhatsApp Cloud API send  ◄────────  text + chart + Excel + PPT
```

WhatsApp uses **webhooks**, not long polling, so the bot runs as a small web service that Meta calls whenever a user sends a message.

---

## Project Layout

```
hmis-telegram-agent/
├── bot.py                      # FastAPI app: webhook, dispatcher, state machine
├── whatsapp_client.py          # WhatsApp Cloud API wrapper (send/upload/download)
├── claude_client.py            # All OpenAI calls
├── bigquery_client.py          # BigQuery wrapper
├── schema_cache.py             # COALESCE-pair detection
├── audit_logger.py             # Per-interaction logging (JSONL + Google Sheet)
├── chart.py                    # Chart generation
├── export_utils.py             # Excel / PowerPoint export
├── completeness.py             # Data completeness checks
├── pre.py                      # Probable Reporting Errors (PREs)
├── config.py                   # Loads .env, validates required vars
│
├── requirements.txt
├── .gitignore
├── .env.example                # Template — copy to .env and fill in
├── service_account.json        # BigQuery credentials (NOT in git)
├── CLAUDE.md
│
├── tools/                      # One-shot utilities
├── tests/
├── docs/
└── data/                       # Runtime data (gitignored, auto-created)
    ├── audit_log.jsonl
    └── schema_coalesce_cache.json
```

---

## Prerequisites

- Python 3.11+
- A **Meta for Developers** account with a configured WhatsApp Business app + verified test number (free)
- An **OpenAI API key** with access to `gpt-5.4` and `gpt-5.4-mini`
- A **Google Cloud service account** JSON with BigQuery Data Viewer + Job User on the HMIS dataset (and Sheets + Drive APIs enabled in the same GCP project for the audit-log sheet)
- A way to expose the bot's HTTP port to the public internet over **HTTPS** (Meta requires HTTPS):
  - Local development: `ngrok` or `cloudflared` tunnel
  - Production: a VM with a public IP + domain + SSL cert (Let's Encrypt via Caddy or nginx)

---

## Quick start

```bash
# 1. Clone and install
cd hmis-telegram-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt

# 2. Configure
cp .env.example .env            # (Windows: copy .env.example .env)
# Edit .env — fill in WHATSAPP_*, OPENAI_API_KEY, GOOGLE_APPLICATION_CREDENTIALS

# 3. Place service_account.json in the project root

# 4. Run
python bot.py
# → bot listens on http://0.0.0.0:8080
```

The full WhatsApp setup steps (registering the app with Meta, getting the access token, configuring the webhook) are below.

---

## Endpoints exposed

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/`        | Health check (returns `{"status":"ok"}`) |
| GET    | `/webhook` | Meta webhook verification handshake (one-time, on setup) |
| POST   | `/webhook` | Inbound WhatsApp messages (Meta → bot) |

---

## Bot Commands (for end users)

WhatsApp doesn't have built-in slash-command UI, but the bot recognises these as plain text:

| Command | What it does |
|---------|--------------|
| `/start` or `start` | Show the welcome message and feature list |
| `/help` or `help` | Show the full feature list |
| `/clear` or `clear` or `/reset` | Reset that user's conversation history |

---

## Running locally with ngrok

For development, you need a public HTTPS URL to give Meta. The easiest way:

```bash
# Terminal 1 — run the bot
python bot.py

# Terminal 2 — expose it via ngrok
ngrok http 8080
# → ngrok prints something like https://abcd-1234.ngrok.io
```

Use that ngrok URL + `/webhook` (so `https://abcd-1234.ngrok.io/webhook`) as the Callback URL in Meta's WhatsApp app dashboard. Restart ngrok and you'll get a fresh URL — re-paste into Meta each time.

---

## Deployment

See [docs/Server Deployment.txt](docs/Server%20Deployment.txt) for the rundown of hosting options. Recommended path:

1. Provision a small GCE VM (e2-small) in the same GCP project as your BigQuery dataset.
2. Buy or reuse a domain (e.g. `hmis-bot.example.in`) and point an A-record at the VM's IP.
3. Install Caddy as a reverse proxy — it auto-issues a Let's Encrypt cert. A 4-line Caddyfile:
   ```
   hmis-bot.example.in {
       reverse_proxy localhost:8080
   }
   ```
4. Clone the repo on the VM, install Python 3.11+, copy `.env` and `service_account.json`.
5. Run as a systemd service:
   ```ini
   # /etc/systemd/system/hmis-bot.service
   [Unit]
   Description=HMIS WhatsApp Bot
   After=network.target

   [Service]
   Type=simple
   User=hmisbot
   WorkingDirectory=/home/hmisbot/hmis-telegram-agent
   EnvironmentFile=/home/hmisbot/hmis-telegram-agent/.env
   ExecStart=/home/hmisbot/hmis-telegram-agent/.venv/bin/python bot.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
6. `sudo systemctl enable --now hmis-bot`. Watch logs with `journalctl -u hmis-bot -f`.

To deploy code changes:
```bash
git pull && sudo systemctl restart hmis-bot
```

The bot blinks for ~5 seconds during a restart. WhatsApp will retry any message Meta couldn't deliver, and the bot's 60-second age filter drops only truly stale messages.

---

## Maintenance Knobs

| File | What to change there |
|------|----------------------|
| `.env` | API keys, WhatsApp tokens, models, port, audit sheet ID |
| [CLAUDE.md](CLAUDE.md) | The system prompt / domain rules baked into every SQL-generation call |
| [config.py](config.py) | Required-variable list, default model IDs, default port |
| [audit_logger.py](audit_logger.py) | Audit log shape, captured backend log filtering |
| [schema_cache.py](schema_cache.py) | COALESCE-pair detection cache TTL (default 7 days) |

---

## Support

mchaurasiya@wjcf.in
