# HMIS WhatsApp Analytics Bot — Project Log & Operating Manual

**Project**: AI conversational analytics agent for Bihar HMIS data, served via WhatsApp.
**Repo**: `https://github.com/zerobug-mohit/ai-conversational-analytics-agent-hmis-bihar` (private)
**Owner**: Mohit Chaurasiya · WJCF · `mchaurasiya@wjcf.in`
**Last major update**: 2026-05-07 (production deployment)
**Status**: Live in production on Google Cloud VM, serving WhatsApp messages via permanent webhook.

---

## Table of Contents

1. [Project overview](#1-project-overview)
2. [Production architecture](#2-production-architecture)
3. [Project file structure](#3-project-file-structure)
4. [Production environment reference](#4-production-environment-reference)
5. [Configuration files reference](#5-configuration-files-reference)
6. [How the bot processes a query (pipeline)](#6-how-the-bot-processes-a-query)
7. [Code change workflow — make + deploy changes](#7-code-change-workflow)
8. [Daily operations runbook](#8-daily-operations-runbook)
9. [Switching domain to a wjcf.in subdomain](#9-switching-domain-to-a-wjcfin-subdomain)
10. [Renewing tokens (recurring tasks)](#10-renewing-tokens)
11. [Troubleshooting guide](#11-troubleshooting-guide)
12. [Fine-tuning the model on real interactions](#12-fine-tuning-the-model)
13. [Cost monitoring](#13-cost-monitoring)
14. [Security checklist](#14-security-checklist)
15. [Useful command cheat sheet](#15-useful-command-cheat-sheet)
16. [Future improvements & known limitations](#16-future-improvements--known-limitations)
17. [Appendix: build history](#17-appendix-build-history)

---

## 1. Project overview

### What the bot does
A WhatsApp bot that lets immunization field officers in Bihar (and later UP) ask plain-language questions about HMIS data and receive:
- A summarized answer (text reply on WhatsApp)
- A chart image (auto-selected chart type)
- A downloadable Excel file with the underlying data
- A downloadable PowerPoint slide with a native editable chart

Users can write or speak in **English, Hindi, or Hinglish** (text or voice notes).

### What kinds of questions
The bot answers questions about:
- Antigen coverage (BCG, FIC, Pentavalent, OPV, IPV, Rotavirus, MR, JE, PCV) at any geographic level
- Trends, rankings, comparisons across geographies and time periods
- ANC, deliveries, maternal-health indicators
- Newborn and child care indicators
- Session performance (sessions held vs planned, ASHA presence)
- Data quality assessments (completeness, correctness, PREs)

### Tech stack
| Layer | Tech | Notes |
|-------|------|-------|
| Chat platform | WhatsApp Cloud API (Meta Graph API v22.0) | Free for the first 1,000 service conversations/month |
| Web framework | FastAPI + uvicorn (Python) | Receives webhook POSTs from Meta |
| Reverse proxy | Caddy | Auto-issues Let's Encrypt SSL certs |
| Process supervisor | systemd | Auto-restarts the bot on crashes |
| Hosting | Google Cloud Compute Engine (e2-small, asia-south1) | Same GCP project as BigQuery |
| LLM | OpenAI `gpt-5.4` (primary) + `gpt-5.4-mini` (fast) | Configurable in `.env` |
| Voice transcription | OpenAI Whisper-1 | English / Hindi / Hinglish |
| Database | Google BigQuery (`biharhmisdatafordvctool.BH_HMIS_Data`) | Read-only access via service account |
| Audit logging | Google Sheets + local JSONL on the VM | Fire-and-forget, never blocks responses |
| Charts | matplotlib | ~25+ chart types, model-selected |
| Exports | openpyxl (Excel), python-pptx (PowerPoint) | Auto-generated for nearly every query |

### Status
- **Production**: live on `https://hmis-bh-bot.duckdns.org/webhook`
- **Test number**: Meta-issued sandbox number `+1 555-630-9203` (allow-listed personal numbers only — see [Section 4](#4-production-environment-reference))
- **Production number**: not yet — will require Meta business verification + WhatsApp Business Account approval

---

## 2. Production architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  WhatsApp user (Mohit's personal phone, allow-listed)                    │
│  Sends text or voice → Meta servers                                       │
└────────────────────────────────────────────┬─────────────────────────────┘
                                              │ HTTPS POST webhook
                                              ▼
                          ┌───────────────────────────────────────┐
                          │  Meta WhatsApp Cloud API              │
                          │  Test number: +1 555-630-9203         │
                          │  Phone Number ID: 1033363533202599    │
                          │  WABA ID: 1001442865550504            │
                          └────────────┬──────────────────────────┘
                                        │ POST https://hmis-bh-bot.duckdns.org/webhook
                                        ▼
                  ┌─────────────────────────────────────────────────┐
                  │  DuckDNS DNS (free)                             │
                  │  hmis-bh-bot.duckdns.org → 34.93.253.59         │
                  └────────────────────────┬────────────────────────┘
                                            │
                                            ▼
                  ┌──────────────────────────────────────────────────┐
                  │  GCE VM "hmis-bot"                               │
                  │  IP: 34.93.253.59 (static, reserved)             │
                  │  Region: asia-south1 (Mumbai)                    │
                  │  OS: Debian 12, Python 3.11.2                    │
                  │                                                   │
                  │  ┌──────────────────────────────────────────┐    │
                  │  │  Caddy (port 443, HTTPS, Let's Encrypt)  │    │
                  │  └──────────────┬───────────────────────────┘    │
                  │                  │ reverse_proxy                  │
                  │                  ▼                                │
                  │  ┌──────────────────────────────────────────┐    │
                  │  │  Bot (FastAPI + uvicorn, port 8080)      │    │
                  │  │  systemd unit: hmis-bot.service           │    │
                  │  │  User: cqasbihar                          │    │
                  │  │  Working dir:                             │    │
                  │  │  /home/cqasbihar/ai-conversational-       │    │
                  │  │  analytics-agent-hmis-bihar/              │    │
                  │  └──────┬───────────────────────────────────┘    │
                  └─────────┼────────────────────────────────────────┘
                            │
                  ┌─────────┼─────────────────────┬─────────────────────┐
                  ▼         ▼                     ▼                     ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐ ┌──────────────┐
        │ OpenAI API   │ │ BigQuery     │ │ Google Sheets    │ │ data/audit_  │
        │ gpt-5.4 +    │ │ HMIS dataset │ │ Audit log sheet  │ │ log.jsonl    │
        │ Whisper-1    │ │ (read-only)  │ │ (per-interaction │ │ (per-interac.│
        │              │ │              │ │ rows)            │ │ JSONL on VM) │
        └──────────────┘ └──────────────┘ └──────────────────┘ └──────────────┘
```

---

## 3. Project file structure

```
ai-conversational-analytics-agent-hmis-bihar/
│
├── bot.py                       # FastAPI app + webhook handlers + state machine
├── whatsapp_client.py           # Meta Graph API wrapper (send/upload/download)
├── claude_client.py             # All OpenAI calls — SQL gen, summarize, charts, etc.
├── bigquery_client.py           # BigQuery wrapper (run_query, get_latest_upload_date)
├── schema_cache.py              # Auto-detects COALESCE pairs from INFORMATION_SCHEMA
├── audit_logger.py              # Per-interaction logging (JSONL + Google Sheet + backend log)
├── chart.py                     # Chart generator (~25 chart types)
├── export_utils.py              # Excel + PowerPoint export
├── completeness.py              # 50-field data completeness logic
├── pre.py                       # Probable Reporting Errors logic
├── config.py                    # Loads .env, validates required vars at startup
│
├── requirements.txt             # Python dependencies pinned
├── .gitignore                   # Excludes .env, service_account.json, data/, etc.
├── .env.example                 # Template — placeholder values only
├── CLAUDE.md                    # Project-level instructions for Claude Code
├── README.md                    # Setup / run / deploy quick reference
│
├── tools/                       # One-shot utilities, run manually
│   ├── export_finetune.py       # Build OpenAI fine-tuning JSONL from audit log
│   ├── whatsapp_diagnose.py     # Check WhatsApp app subscription / token
│   ├── generate_flowchart.py    # Render the architecture flowchart PNG
│   └── generate_cost_analysis.py# Render the cost-model spreadsheet
│
├── tests/
│   └── test_pipeline.py         # Smoke-test the full pipeline without Telegram
│
├── docs/
│   ├── PROJECT_LOG.md           # ← This file
│   └── Server Deployment.txt    # Notes on hosting options (older)
│
├── data/                        # Runtime files (gitignored, auto-created)
│   ├── audit_log.jsonl          # Append-only interaction log (every query + reply)
│   └── schema_coalesce_cache.json  # Cached COALESCE-pair detection
│
├── .env                         # ← LOCAL SECRETS — never committed (gitignored)
└── service_account.json         # ← BIGQUERY CREDENTIALS — never committed (gitignored)
```

### File-by-file purpose
| File | What it does |
|------|--------------|
| `bot.py` | Entry point. Defines FastAPI app with `GET /webhook` (verification handshake), `POST /webhook` (inbound messages), `GET /` (health check). Holds the per-user state machine (idle / awaiting clarification / awaiting confirmation / awaiting SQL clarification / awaiting help choice). Routes voice and text messages through `_process_message`. Handles `/help`, `/start`, `/clear` commands. Appends the global "Type /help to know more..." footnote to non-help messages. |
| `whatsapp_client.py` | Async wrapper around Meta Graph API. Functions: `send_text`, `send_image`, `send_document`, `upload_media`, `download_media`, `mark_as_read`, `verify_signature`. |
| `claude_client.py` | All OpenAI API calls. Functions: `check_query_relevance`, `extract_geo_entity`, `generate_sql`, `fix_sql`, `diagnose_sql_failure`, `summarize_results`, `generate_key_findings`, `get_chart_spec`, `formulate_understanding`, `check_if_confirmed`, `synthesize_final_query`, `answer_conversational`, `transcribe_voice`. Holds the system prompt `_SYSTEM_PROMPT` (~48k chars) with all 14+ rules. |
| `bigquery_client.py` | Synchronous BigQuery wrapper. `run_query(sql)` and `get_latest_upload_date()`. Authenticates via `GOOGLE_APPLICATION_CREDENTIALS` env var. |
| `schema_cache.py` | On bot startup, queries `INFORMATION_SCHEMA.COLUMNS` to get the current 283-column list, then either reads cached COALESCE pairs from `data/schema_coalesce_cache.json` or asks the LLM to detect them. Builds the "live COALESCE pairs" injection text used in the SQL system prompt. |
| `audit_logger.py` | Per-interaction logging. `log_interaction(...)` writes one JSONL record + one Google Sheet row per user message. Also installs a logging handler that captures the backend log per request via `contextvars` and includes it as a column. |
| `chart.py` | Chart generator. Reads chart spec from `claude_client.get_chart_spec`, renders matplotlib figure, returns PNG bytes. |
| `export_utils.py` | Excel + PowerPoint generators. Uses openpyxl for Excel (formatted headers, styling), python-pptx for slides (with native chart). |
| `completeness.py` | 50-field data completeness logic. Used by the LLM via the system prompt — code is reference for the SQL pattern documentation. |
| `pre.py` | 16-check Probable Reporting Errors logic. Same — used as documentation reference. |
| `config.py` | Loads `.env` via python-dotenv. Validates required vars at startup, fails fast with clear error if any missing. |

---

## 4. Production environment reference

### Cloud
| Item | Value |
|------|-------|
| Provider | Google Cloud Platform |
| Project ID | `biharhmisdatafordvctool` |
| Region | `asia-south1` (Mumbai) |
| Zone | `asia-south1-a` |
| VM name | `hmis-bot` |
| Machine type | `e2-small` (2 vCPU, 2 GB RAM) |
| OS | Debian 12 (Bookworm), kernel 6.1 |
| Boot disk | 20 GB Standard persistent disk |
| Static external IP | `34.93.253.59` (named `hmis-bot-ip` in GCP) |
| SSH user | `cqasbihar` |
| Project working dir | `/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar/` |

### DNS & TLS
| Item | Value |
|------|-------|
| Domain (current) | `hmis-bh-bot.duckdns.org` |
| DNS provider | DuckDNS (free) |
| TLS cert | Let's Encrypt (auto-renewed by Caddy 30 days before expiry) |
| Webhook URL | `https://hmis-bh-bot.duckdns.org/webhook` |
| Health check URL | `https://hmis-bh-bot.duckdns.org/` (returns `{"status":"ok",...}`) |
| Future domain (planned) | `hmis-bot.wjcf.in` (or similar) — see [Section 9](#9-switching-domain-to-a-wjcfin-subdomain) |

### Code
| Item | Value |
|------|-------|
| GitHub repo | `https://github.com/zerobug-mohit/ai-conversational-analytics-agent-hmis-bihar` |
| Visibility | Private |
| Default branch | `main` |
| Deployment method | `git pull` on the VM, then `systemctl restart hmis-bot` |
| Local clone path | `c:\Users\mchaurasiya\hmis-telegram-agent\` |

### WhatsApp / Meta
| Item | Value |
|------|-------|
| Meta App | Created via developers.facebook.com |
| WhatsApp Business Account ID (WABA) | `1001442865550504` |
| Phone Number ID | `1033363533202599` (this is what the bot sends `from`) |
| Test phone number | `+1 555-630-9203` |
| Webhook callback URL | `https://hmis-bh-bot.duckdns.org/webhook` |
| Subscribed events | `messages` (only this one) |
| Allow-listed recipients | Mohit's personal WhatsApp number |
| App is subscribed to WABA via | `/v22.0/{WABA_ID}/subscribed_apps` (run `tools/whatsapp_diagnose.py --fix` if ever lost) |

### Secrets — where each lives
None of these are in git.

| Secret | On laptop | On VM | Source |
|--------|-----------|-------|--------|
| `WHATSAPP_ACCESS_TOKEN` (System User permanent) | `c:\...\hmis-telegram-agent\.env` | `/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar/.env` | Generate at Business Settings → System Users |
| `WHATSAPP_APP_SECRET` | same `.env` | same `.env` on VM | App Settings → Basic → App Secret → Show |
| `WHATSAPP_VERIFY_TOKEN` | same `.env` | same `.env` on VM | You picked this string yourself; must match Meta Configuration |
| `WHATSAPP_PHONE_NUMBER_ID` | same `.env` | same `.env` on VM | WhatsApp → API Setup |
| `OPENAI_API_KEY` | same `.env` | same `.env` on VM | platform.openai.com → API keys |
| `GOOGLE_APPLICATION_CREDENTIALS` (filename only) | same `.env` (points to `service_account.json`) | same on VM | — |
| `service_account.json` (BigQuery key) | `c:\...\hmis-telegram-agent\service_account.json` | `/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar/service_account.json` | GCP IAM → service accounts → Keys → Add Key |
| `AUDIT_SHEET_ID` | same `.env` | same `.env` on VM | URL of the Google Sheet (the part between `/d/` and `/edit`) |
| GitHub Personal Access Token (for git pull on VM) | not stored locally | cached at `/home/cqasbihar/.git-credentials` (chmod 600) | Regenerate at `github.com/settings/personal-access-tokens` (fine-grained, read-only, scoped to this repo) |

### Token expirations to track
| Token | Lifetime | Where to renew |
|-------|----------|----------------|
| GitHub PAT | 90 days from generation | `github.com/settings/personal-access-tokens` → regenerate; on VM, run `rm ~/.git-credentials && git pull` to re-prompt |
| WhatsApp System User access token | "Never" if generated as permanent | Business Settings → System Users → tokens; only renew if revoked |
| WhatsApp temporary access token | 24 hours | API Setup page (not used in production) |
| Let's Encrypt SSL cert | 90 days | **Auto-renewed by Caddy** 30 days before expiry — nothing to do |
| OpenAI API key | No expiry | Rotate manually if leaked |

---

## 5. Configuration files reference

### 5.1 `.env` (local + VM — same content, both must be in sync)
```dotenv
# WhatsApp Cloud API
WHATSAPP_ACCESS_TOKEN=<permanent System User token>
WHATSAPP_PHONE_NUMBER_ID=1033363533202599
WHATSAPP_VERIFY_TOKEN=ai-conversational-agent-for-hmis-bihar
WHATSAPP_APP_SECRET=<App Secret from Meta App Settings>
WHATSAPP_GRAPH_VERSION=v22.0

# Web server
SERVER_HOST=0.0.0.0
SERVER_PORT=8080

# OpenAI
OPENAI_API_KEY=sk-proj-<your key>
AI_MODEL_SQL=gpt-5.4
AI_MODEL_FAST=gpt-5.4-mini

# BigQuery
GOOGLE_APPLICATION_CREDENTIALS=service_account.json
BIGQUERY_PROJECT_ID=biharhmisdatafordvctool

# Conversation
MAX_HISTORY_EXCHANGES=10

# Audit logging
AUDIT_SHEET_ID=<Google Sheet ID>
```

When `.env` changes locally, you must also update it on the VM (it's not in git). See [Section 7.5](#75-deploying-env-changes-when-needed).

### 5.2 `.env.example` (in git)
Template with placeholder values. Never put real secrets here.

### 5.3 systemd service file — `/etc/systemd/system/hmis-bot.service` (on VM)
```ini
[Unit]
Description=HMIS WhatsApp Analytics Bot
After=network.target

[Service]
Type=simple
User=cqasbihar
WorkingDirectory=/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar
ExecStart=/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar/.venv/bin/python bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 5.4 `/etc/caddy/Caddyfile` (on VM)
```caddyfile
hmis-bh-bot.duckdns.org {
    reverse_proxy localhost:8080
}
```

When swapping to wjcf.in subdomain, this file gets one line edited.

---

## 6. How the bot processes a query

For a typical text query like "What is BCG coverage in Patna for March 2026?":

```
 1. Meta POSTs webhook  →  bot.py receive_webhook()
 2. Signature verified using WHATSAPP_APP_SECRET
 3. Message dispatched to _process_message in a background asyncio task
 4. mark_as_read sent (best-effort, shows blue ticks)
 5. Built-in commands check (/help, /start, /clear) — none here
 6. Per-user state machine — user is in "idle" → _handle_new_question
 7. claude_client.check_query_relevance — gpt-5.4-mini call
    Returns: {is_relevant: true, needs_sql: true}
 8. claude_client.extract_geo_entity — gpt-5.4-mini call
    Returns: {entities: [{geo_type: "district", user_input_name: "Patna",
                          lookup_sql: "SELECT DISTINCT district_name,..."}]}
 9. bigquery_client.run_query(lookup_sql) — finds 1 match: Patna (code 197)
10. claude_client.formulate_understanding — gpt-5.4 call
    Returns: "I'll look up BCG coverage in Patna district (code 197) for
              March 2026 — confirm?"
11. State → awaiting_confirmation. Reply sent. Footnote appended.
12. User replies "yes"
13. claude_client.check_if_confirmed — gpt-5.4-mini call → confirmed
14. claude_client.synthesize_final_query — gpt-5.4 call
    Returns: "BCG coverage in Patna district for March 2026"
15. claude_client.generate_sql — gpt-5.4 call
    Returns the SQL with proper district_targets_estimated_infants_202425 etc.
16. bigquery_client.run_query(sql) — gets the result rows
17. Three calls in parallel (asyncio.gather):
    - claude_client.summarize_results — gpt-5.4 (Answer + What the agent did)
    - claude_client.generate_key_findings — gpt-5.4 (Key Findings bullets)
    - claude_client.get_chart_spec — gpt-5.4 (chart_type + columns + labels)
18. chart.generate_chart(spec, results) — produces PNG bytes
19. export_utils.generate_excel — produces XLSX bytes (always for tabular data)
20. export_utils.generate_ppt — produces PPTX bytes (when chart exists)
21. audit_logger.log_interaction — fire-and-forget background write to
     Google Sheet + data/audit_log.jsonl. Includes captured backend log.
22. Bot replies on WhatsApp via whatsapp_client:
    - send_text (the answer + footnote)
    - upload_media + send_image (the chart)
    - upload_media + send_document (Excel)
    - upload_media + send_document (PowerPoint)
```

Total time: ~15–30 seconds for a fresh query, mostly OpenAI latency. Three parallel calls in step 17 save time.

The same flow applies to voice notes — `_process_voice` first downloads audio, transcribes via Whisper, then routes the transcribed text into `_process_message`.

---

## 7. Code change workflow

### 7.1 Edit code locally
Open the project in your editor (VS Code) at `c:\Users\mchaurasiya\hmis-telegram-agent\`. Make changes to any `.py` file, `CLAUDE.md`, `README.md`, etc.

### 7.2 (Optional) Test changes locally before deploying
For most simple changes (prompt tweaks, formatting fixes), it's faster to deploy directly and watch logs. For risky changes (new SQL rules, state-machine logic), test locally first.

To test locally:
```powershell
# Terminal 1
cd c:\Users\mchaurasiya\hmis-telegram-agent
python bot.py

# Terminal 2
ngrok http 8080
```

Then temporarily change Meta's webhook URL to the ngrok URL, send messages, observe behavior. Switch back to `https://hmis-bh-bot.duckdns.org/webhook` after testing. (Most of the time you can skip this step.)

### 7.3 Commit + push to GitHub
```powershell
cd c:\Users\mchaurasiya\hmis-telegram-agent
git status                    # see what changed
git add .                     # stage everything
git commit -m "Brief description of what changed"
git push                      # push to GitHub
```

The first push after the PAT expires will prompt for credentials again — paste the new PAT.

### 7.4 Deploy to production (VM)
SSH into the VM via browser SSH (https://console.cloud.google.com/compute/instances → SSH next to `hmis-bot`).

```bash
cd ~/ai-conversational-analytics-agent-hmis-bihar
git pull
sudo systemctl restart hmis-bot
sudo systemctl status hmis-bot --no-pager
```

Watch the status output for `Active: active (running)`.

If the change added a new dependency to `requirements.txt`:
```bash
source .venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart hmis-bot
```

**Downtime**: ~5–10 seconds during the restart. WhatsApp queues messages; the bot's 60-second age filter accepts them on resume.

### 7.5 Deploying `.env` changes (when needed)
The `.env` is gitignored — code-deploy doesn't update it. For changes:

**Option A — full re-upload** (any change):
1. Edit `.env` locally
2. SSH to VM in browser
3. In SSH UI, top-right gear icon → Upload file → pick the new local `.env`
4. On VM:
   ```bash
   mv ~/.env ~/ai-conversational-analytics-agent-hmis-bihar/.env
   chmod 600 ~/ai-conversational-analytics-agent-hmis-bihar/.env
   sudo systemctl restart hmis-bot
   ```

**Option B — single-line change directly on VM** (small tweak):
```bash
nano ~/ai-conversational-analytics-agent-hmis-bihar/.env
# Edit the line, save with Ctrl+O Enter Ctrl+X
sudo systemctl restart hmis-bot
```
Remember to also update the local `.env` to match — they should never drift.

### 7.6 Rolling back a bad deploy
If a deploy breaks production (errors in logs, bot stops responding), revert:

```bash
# On VM
cd ~/ai-conversational-analytics-agent-hmis-bihar
git log --oneline -5             # see last 5 commits
git reset --hard <previous-good-commit-hash>
sudo systemctl restart hmis-bot
```

Then fix the issue locally, commit, push, deploy again.

If you need to push the rollback to GitHub too (so local matches), on your laptop:
```powershell
git fetch origin
git reset --hard origin/main     # match what's currently on GitHub
```

---

## 8. Daily operations runbook

### 8.1 Check bot health
From any device with curl/browser:
```
https://hmis-bh-bot.duckdns.org/
```
Should return `{"status":"ok","service":"HMIS WhatsApp Bot"}`. If not — see [Troubleshooting](#11-troubleshooting-guide).

### 8.2 View logs
```bash
# SSH into the VM, then:

# Real-time tail (Ctrl+C to stop watching, doesn't stop the bot)
sudo journalctl -u hmis-bot -f --no-pager

# Last 100 lines
sudo journalctl -u hmis-bot -n 100 --no-pager

# Filter by time
sudo journalctl -u hmis-bot --since "1 hour ago" --no-pager
sudo journalctl -u hmis-bot --since "2026-05-07 09:00" --no-pager
sudo journalctl -u hmis-bot --since today --no-pager

# Search for errors
sudo journalctl -u hmis-bot --since today | grep -i error

# Caddy logs (rare to need)
sudo journalctl -u caddy -n 50 --no-pager
```

The audit log JSONL is also useful:
```bash
tail -10 ~/ai-conversational-analytics-agent-hmis-bihar/data/audit_log.jsonl
```
This file grows over time — see [Section 13](#13-cost-monitoring) for rotation guidance.

### 8.3 Restart the bot
```bash
sudo systemctl restart hmis-bot
sudo systemctl status hmis-bot --no-pager
```

### 8.4 Stop / start (rare — for maintenance)
```bash
sudo systemctl stop hmis-bot       # bot offline
sudo systemctl start hmis-bot      # bot back online
sudo systemctl disable hmis-bot    # don't start on boot (rare)
sudo systemctl enable hmis-bot     # start on boot (default after install)
```

### 8.5 SSH into the VM
- Browser: https://console.cloud.google.com/compute/instances → click SSH next to `hmis-bot`
- Local with gcloud (if installed):
  ```powershell
  gcloud compute ssh cqasbihar@hmis-bot --zone=asia-south1-a
  ```

### 8.6 Audit log review
- Open the Google Sheet (linked via `AUDIT_SHEET_ID` in `.env`) for human-readable per-interaction rows
- Each row includes: timestamp, user, message, generated SQL, success/failure, processing time, full backend log
- Manually mark good interactions in the **`✓ Good Example`** column with `TRUE` for fine-tuning later (see [Section 12](#12-fine-tuning-the-model))

---

## 9. Switching domain to a wjcf.in subdomain

Once your IT contact adds an A-record like `hmis-bot.wjcf.in → 34.93.253.59` (the static IP):

**Step 1 — verify DNS propagated** (from your laptop):
```powershell
nslookup hmis-bot.wjcf.in
# Should return: Address: 34.93.253.59
```

**Step 2 — edit Caddyfile on the VM**:
```bash
sudo nano /etc/caddy/Caddyfile
```
Change the first line from:
```
hmis-bh-bot.duckdns.org {
```
to:
```
hmis-bot.wjcf.in {
```
(Or list both, separated by commas, to keep DuckDNS as backup: `hmis-bot.wjcf.in, hmis-bh-bot.duckdns.org {`.)

Save: Ctrl+O, Enter, Ctrl+X.

**Step 3 — validate + reload Caddy**:
```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30 --no-pager
```
Look for `obtaining certificate` then `certificate obtained successfully` for the new domain.

**Step 4 — test from laptop**:
```powershell
curl.exe https://hmis-bot.wjcf.in/
# Should return: {"status":"ok","service":"HMIS WhatsApp Bot"}
```

**Step 5 — update Meta webhook URL**:
- developers.facebook.com → your app → WhatsApp → Configuration → Edit
- Callback URL: `https://hmis-bot.wjcf.in/webhook`
- Verify token: same as before
- Verify and save

**Step 6 — send a test from your phone** to confirm the new URL works.

**Step 7 — (optional) remove DuckDNS** from the Caddyfile if you don't want to maintain it. You can also keep it in parallel — costs nothing.

---

## 10. Renewing tokens

### 10.1 GitHub PAT (every 90 days)
The fine-grained PAT we generated for the VM is read-only and scoped to one repo. When it expires:

1. Go to https://github.com/settings/personal-access-tokens
2. Click **Regenerate** on the existing `hmis-bot-vm-deploy` token (extends another 90 days), OR generate a fresh one with the same settings (Contents: Read-only, repo: `ai-conversational-analytics-agent-hmis-bihar`)
3. Copy the new PAT
4. On the VM:
   ```bash
   rm ~/.git-credentials
   cd ~/ai-conversational-analytics-agent-hmis-bihar
   git pull
   # Prompts for username (zerobug-mohit) + password (paste new PAT)
   # credential.helper store re-saves it
   ```

### 10.2 WhatsApp tokens
- **Permanent System User token**: doesn't expire automatically. Only rotate if leaked. To rotate: Business Settings → System Users → click user → Generate New Token. Replace `WHATSAPP_ACCESS_TOKEN` in `.env` on the VM. Restart the bot.
- **Temporary token (the 24-hour one from API Setup)**: not used in production.

### 10.3 OpenAI API key
- Doesn't expire. Rotate if leaked.
- Replace `OPENAI_API_KEY` in `.env`. Restart the bot.

### 10.4 Let's Encrypt SSL cert
**Auto-renewed by Caddy** 30 days before expiry. No manual action.

To verify renewals are working occasionally:
```bash
sudo journalctl -u caddy --since "1 month ago" | grep -i renew
```

### 10.5 BigQuery service account
- Service account keys can be rotated via GCP IAM. Generate a new key, download the new `service_account.json`, replace on VM (and locally), restart bot.
- Recommended every ~6 months as a defensive practice. The current key has full read access to the HMIS dataset — if leaked, an attacker could query (but not modify) data. Rotate if you suspect compromise.

---

## 11. Troubleshooting guide

### 11.1 Bot not responding to WhatsApp messages

**Step 1**: Check the bot is running.
```bash
sudo systemctl status hmis-bot --no-pager
```
- `Active: active (running)` → bot is up. Continue to Step 2.
- `Active: failed` or `Active: inactive` → see [11.2](#112-bot-crashes-or-restarts-continuously).

**Step 2**: Check the public endpoint is reachable.
```powershell
curl.exe https://hmis-bh-bot.duckdns.org/
```
- Returns `{"status":"ok",...}` → bot is reachable. Continue to Step 3.
- Returns connection refused / timeout → see [11.4](#114-public-endpoint-not-reachable).

**Step 3**: Check Meta is delivering webhooks.
- Send a test message from your phone
- On the VM:
  ```bash
  sudo journalctl -u hmis-bot -f --no-pager
  ```
- Within 5 seconds of sending, you should see `POST /webhook ... 200 OK`
- If nothing arrives → see [11.3](#113-meta-not-delivering-webhooks).

### 11.2 Bot crashes or restarts continuously

**Read the error**:
```bash
sudo journalctl -u hmis-bot -n 100 --no-pager
```

Common errors and fixes:

| Error in log | Cause | Fix |
|--------------|-------|-----|
| `[CONFIG ERROR] Missing required environment variable: WHATSAPP_ACCESS_TOKEN` | `.env` missing or has empty values | Verify `~/ai-conversational-analytics-agent-hmis-bihar/.env` exists and has all required vars |
| `FileNotFoundError: service_account.json` | BQ credentials file missing or wrong path | Verify file exists in project dir; check `GOOGLE_APPLICATION_CREDENTIALS` in `.env` matches the filename |
| `google.auth.exceptions.DefaultCredentialsError` | service account JSON corrupted or revoked | Re-download from GCP IAM, re-upload to VM |
| `httpx.ConnectError: [Errno 111] Connection refused` calling Meta | Network issue or Meta API outage | Check status.metaforbusiness.com; usually transient |
| `RuntimeError: 400 Bad Request` from BigQuery | Bad SQL generated | Look at the generated SQL in the audit log; usually self-corrects via fix_sql |
| `openai.AuthenticationError` | OPENAI_API_KEY invalid or revoked | Generate new key, update `.env`, restart |
| `Address already in use` | Port 8080 already used | `sudo systemctl stop hmis-bot; sleep 2; sudo systemctl start hmis-bot` |
| `MemoryError` or OOM-killed | VM out of RAM (rare on e2-small) | Upgrade VM tier (e2-medium 4GB) |

If systemd is restarting it constantly, consider stopping while you debug:
```bash
sudo systemctl stop hmis-bot
# Run manually in foreground to see the full error
cd ~/ai-conversational-analytics-agent-hmis-bihar
source .venv/bin/activate
python bot.py
# When fixed:
sudo systemctl start hmis-bot
```

### 11.3 Meta not delivering webhooks
Symptoms: bot is running, public endpoint works, but no `POST /webhook` lines in logs when you send a message.

**Step 1**: Check the WhatsApp app is subscribed to the WABA.
```bash
cd ~/ai-conversational-analytics-agent-hmis-bihar
source .venv/bin/activate
python tools/whatsapp_diagnose.py
```
If it shows only Meta's "WA DevX Webhook Events 1P App" subscribed (not your `HMIS AI Analytics Agent`), run:
```bash
python tools/whatsapp_diagnose.py --fix
```

**Step 2**: Check Meta dashboard webhook config.
- Callback URL is `https://hmis-bh-bot.duckdns.org/webhook` (with `/webhook`)
- Verify token matches `WHATSAPP_VERIFY_TOKEN` in `.env`
- `messages` field shows **Subscribed**

**Step 3**: Check the chat number on your phone matches the test number on API Setup.
- Meta's test number: `+1 555-630-9203`
- Your phone's chat: tap contact name at top of chat — should be the same `+1 555-630-9203`

**Step 4**: Check your personal phone is on the allow-list.
- API Setup page → To dropdown → must include your number

**Step 5**: Check Meta's recent deliveries log.
- WhatsApp → Configuration → Recent Deliveries (or similar — UI varies)
- Look for `200 OK` next to recent webhook attempts. If `403`/`5xx`, paste it for diagnosis.

### 11.4 Public endpoint not reachable
Symptoms: `curl https://hmis-bh-bot.duckdns.org/` fails with connection refused / timeout / SSL error.

**Step 1**: Caddy running?
```bash
sudo systemctl status caddy --no-pager
```
If not, `sudo systemctl start caddy`.

**Step 2**: VM external IP unchanged?
- GCP Console → VM Instances → check the External IP for `hmis-bot`
- Should still be `34.93.253.59`. If different, the static IP reservation got removed. Re-reserve.

**Step 3**: DNS still resolves correctly?
```powershell
nslookup hmis-bh-bot.duckdns.org
# Should return: Address: 34.93.253.59
```
If wrong, log into duckdns.org and re-update the IP.

**Step 4**: Firewall rules in place?
- GCP Console → VPC network → Firewall
- Should have `default-allow-http` (port 80) and `default-allow-https` (port 443)
- VM should have tags `http-server` and `https-server` (check the VM's Network tags)

**Step 5**: Cert valid?
```bash
sudo journalctl -u caddy -n 50 --no-pager | grep -i error
```
Look for cert renewal errors.

### 11.5 BigQuery query failures
Symptoms: bot replies "The conversational analytics AI agent is facing an error..." for queries that should work.

```bash
sudo journalctl -u hmis-bot -f --no-pager
# Look for: BigQuery error for user XXXXX: ...
```

The bot has 3-attempt auto-retry with `fix_sql`. If still fails:
- Check service account hasn't been deleted: GCP IAM → service accounts → look for the one matching `service_account.json`
- Check BigQuery API still enabled in the project
- Check the table still exists: `BH_HMIS_Facility_Level_Data_with_all_Targets`

### 11.6 Audit log sheet stops updating
- Check Google Sheets API quotas (rare to hit)
- Check `AUDIT_SHEET_ID` in `.env` matches the actual sheet
- Check service account has Edit access to the sheet (share the sheet with the service account email)
- The JSONL file in `data/` should still be growing — that's the local fallback

### 11.7 Disk fills up
```bash
df -h
# Look at /home or root filesystem usage

# What's taking space:
du -sh ~/ai-conversational-analytics-agent-hmis-bihar/* | sort -h
```

Most likely culprit: `data/audit_log.jsonl` grows over time. Manual rotation:
```bash
cd ~/ai-conversational-analytics-agent-hmis-bihar/data
mv audit_log.jsonl audit_log.jsonl.$(date +%Y%m%d)
gzip audit_log.jsonl.*
# Bot creates a fresh audit_log.jsonl on next interaction
```

If disk hits 90%+ on a 20GB VM, consider expanding the boot disk in GCP Console → VM → Edit → Boot disk → Size.

### 11.8 Bot is slow / queries take >60 seconds
Usual causes:
- OpenAI API latency spike (transient)
- BigQuery query is genuinely slow (large geo + long time range)
- Schema cache rebuild on first start after a restart (one-time, ~30s)

To check what's slow, look at log timestamps for a single user's pipeline:
```bash
sudo journalctl -u hmis-bot --since "5 minutes ago" | grep -i "user 91XXXXXXXXXX"
```
Compare timestamps between `Generating SQL`, `Running BigQuery query`, and `Summarizing` — find the slow leg.

---

## 12. Fine-tuning the model

The bot logs every successful interaction with the full SQL and response. You can use this data to fine-tune `gpt-5.4-mini` on real questions, making the bot faster, cheaper, and more aligned with how field officers actually phrase questions in Hindi/Hinglish.

### 12.1 What gets logged
For every interaction, both Google Sheet and `data/audit_log.jsonl` capture:
- `user_message` — exactly what the user wrote
- `geo_entity` — confirmed geo lookup result
- `generated_sql` — the SQL the bot ran
- `bq_row_count` — how many rows came back
- `response_text` — what the bot replied
- `success`, `error_message`, `processing_time_ms`
- `backend_log` — full pipeline log for that interaction
- (Manual) `✓ Good Example` column in the Sheet — you tick this to mark gold examples

### 12.2 Curating good examples
Open the Google Sheet weekly. For each row, ask:
- Was the answer correct?
- Was the SQL the bot generated good?
- Would I want the model to learn from this exact (question, SQL) pair?

If yes — set the **`✓ Good Example`** column to `TRUE`. Mark **only**:
- Realistic phrasings field officers actually use (Hindi, Hinglish, abbreviations)
- Successful follow-ups ("now show me for Gaya", "what about last year?")
- Edge cases that worked (vague queries, ambiguous geos resolved correctly)

Skip:
- Generic English textbook phrasings (the base model already handles these)
- Queries where the bot needed multiple corrections
- Errors, CANNOT_GENERATE responses, methodology questions

### 12.3 Build the fine-tune dataset
On your laptop (after running the bot for ~2-4 weeks and curating):

```powershell
cd c:\Users\mchaurasiya\hmis-telegram-agent

# Download the audit log from the VM (one-time):
gcloud compute scp hmis-bot:~/ai-conversational-analytics-agent-hmis-bihar/data/audit_log.jsonl `
                  data\audit_log.jsonl --zone=asia-south1-a

# Export the Google Sheet as CSV (File → Download → Comma-separated values),
# save it as audit_sheet.csv in the project folder

# Run the export tool — only includes rows with ✓ Good Example = TRUE
python tools\export_finetune.py --task sql --curated audit_sheet.csv
# Produces: finetune_sql_<timestamp>.jsonl
```

Inspect the output JSONL — make sure it has at least 50 examples (OpenAI's minimum).

### 12.4 Run the fine-tune on OpenAI
```powershell
# Upload the file
openai api fine_tuning.jobs.create -t finetune_sql_<timestamp>.jsonl -m gpt-5.4-mini

# Or via the OpenAI dashboard at platform.openai.com → Fine-tuning → Create
```

Wait ~30-60 minutes for the job to finish. You'll get a fine-tuned model ID like `ft:gpt-5.4-mini-2026-XX-XX:wjcf::abc123`.

### 12.5 Switch to the fine-tuned model
On the VM:
```bash
nano ~/ai-conversational-analytics-agent-hmis-bihar/.env
# Change: AI_MODEL_FAST=gpt-5.4-mini
# To:     AI_MODEL_FAST=ft:gpt-5.4-mini-2026-XX-XX:wjcf::abc123
sudo systemctl restart hmis-bot
```
Send a few test messages. If quality is bad, revert by setting back to `gpt-5.4-mini` and restart.

### 12.6 Track quality over time
After switching, watch the audit log for ~1 week:
- Did query accuracy improve?
- Did response time decrease?
- Did costs drop?

If yes, keep the fine-tuned model. Periodically (every 1–2 months), do another curation round and re-fine-tune to incorporate new patterns.

---

## 13. Cost monitoring

### Per-month estimates (approximate, INR)
| Item | Estimated cost | Where to monitor |
|------|----------------|-------------------|
| GCE VM (e2-small, asia-south1) | ₹1,300–1,500/mo | GCP Console → Billing |
| GCP egress (network out) | ₹0–50/mo (light usage) | GCP Billing |
| BigQuery queries | ₹0–500/mo (lookup queries are tiny; on-demand pricing) | GCP Billing → BigQuery |
| Google Sheets API | Free | — |
| OpenAI gpt-5.4 + gpt-5.4-mini | ₹500–2,000/mo (depends on query volume) | platform.openai.com → Usage |
| OpenAI Whisper (voice) | ~₹0.50/voice note (~10s) | platform.openai.com |
| WhatsApp Cloud API | Free for first 1,000 service convos/month, then ~₹0.30–₹0.80/convo in India | Meta Business Settings → Billing |
| DuckDNS | Free | — |
| Future: real domain (.in) | ~₹500–800/year | Domain registrar |
| **Total monthly estimate** | **~₹2,000–4,000/mo** | |

### Set up budget alerts
**GCP**: Console → Billing → Budgets & alerts → Create budget. Set monthly budget at ₹3,000 with alerts at 50% / 90% / 100%.

**OpenAI**: platform.openai.com → Settings → Billing → Limits. Set monthly limit at $30 (approx ₹2,500) with auto-pause when exceeded.

### Monitor disk usage
```bash
df -h /home              # check VM disk
ls -lh ~/ai-conversational-analytics-agent-hmis-bihar/data/audit_log.jsonl
```
Rotate `audit_log.jsonl` when it exceeds ~5 GB (see [11.7](#117-disk-fills-up)).

### Monitor Meta conversation count
Meta dashboard → WhatsApp → Insights. Free tier is 1,000 service conversations/month per WABA. Track usage; if approaching the limit, consider scaling pricing.

---

## 14. Security checklist

### 14.1 What's in git, what's not
**In git** (safe — public-ish info):
- All `.py` source files
- `CLAUDE.md`, `README.md`, `requirements.txt`, `.gitignore`, `.env.example`
- Docs and tools

**NOT in git** (gitignored — must never be committed):
- `.env` (real secrets)
- `service_account.json` (BigQuery credentials)
- `data/` directory (audit log, cache)
- `__pycache__/`, `.venv/`
- `*.xlsx`, `hmis_bot_flowchart.png`

### 14.2 What's on the VM, with what permissions
| File | Path on VM | Permissions |
|------|------------|-------------|
| `.env` | `/home/cqasbihar/ai-conversational-analytics-agent-hmis-bihar/.env` | `0600` (owner read/write only) |
| `service_account.json` | same dir | `0600` |
| Project code | same dir | normal |
| systemd unit | `/etc/systemd/system/hmis-bot.service` | `0644` (root-owned, world-readable) |
| Caddyfile | `/etc/caddy/Caddyfile` | `0644` |
| Caddy SSL certs | `/var/lib/caddy/.local/share/caddy/...` | managed by Caddy, root-owned |
| GitHub PAT (cached) | `/home/cqasbihar/.git-credentials` | `0600` |

### 14.3 Public-facing surface
- Port 80 (Caddy redirects to 443)
- Port 443 (Caddy → reverse proxy → bot's port 8080)
- Port 22 (SSH — restricted to GCP-authenticated browser SSH and your gcloud user)
- Port 8080 is **not** open to the internet (only Caddy on the same machine talks to it)

### 14.4 Rotation schedule (suggested)
- GitHub PAT — every 90 days (forced by expiration)
- WhatsApp permanent token — every 6–12 months (defensive)
- BigQuery service account key — every 6–12 months (defensive)
- OpenAI API key — only on suspicion of leak, or when an employee leaves

### 14.5 What to monitor for compromise
- Check audit log for unexpected user phone numbers (someone else messaging the bot)
- Check GCP billing for anomalous costs (unusual BQ usage, network egress)
- Check OpenAI usage for sudden spikes
- Check `sudo last` on the VM for unexpected logins (only your gcloud user should appear)
- Check `sudo journalctl --since "1 day ago"` for `Failed password` or `Invalid user` (SSH brute-force attempts — usually harmless because SSH is restricted to GCP auth only)

---

## 15. Useful command cheat sheet

### Local (PowerShell, on laptop)
```powershell
# Run bot locally for development
python bot.py

# Expose to internet via ngrok (requires ngrok 3.20+)
ngrok http 8080

# Git workflow
git status
git add .
git commit -m "..."
git push

# Smoke test the pipeline without WhatsApp
python tests\test_pipeline.py

# Build fine-tuning dataset
python tools\export_finetune.py --task sql --curated audit_sheet.csv

# Diagnose WhatsApp setup
python tools\whatsapp_diagnose.py            # check
python tools\whatsapp_diagnose.py --fix      # auto-subscribe app to WABA

# DNS check
nslookup hmis-bh-bot.duckdns.org

# Health check production
curl.exe https://hmis-bh-bot.duckdns.org/
```

### VM (Bash, via browser SSH)
```bash
# Bot lifecycle
sudo systemctl status hmis-bot --no-pager
sudo systemctl restart hmis-bot
sudo systemctl stop hmis-bot
sudo systemctl start hmis-bot

# Logs
sudo journalctl -u hmis-bot -f --no-pager
sudo journalctl -u hmis-bot -n 100 --no-pager
sudo journalctl -u hmis-bot --since "1 hour ago" --no-pager
sudo journalctl -u hmis-bot --since today | grep -i error

# Caddy
sudo systemctl status caddy --no-pager
sudo systemctl reload caddy
sudo journalctl -u caddy -n 50 --no-pager

# Deploy code change
cd ~/ai-conversational-analytics-agent-hmis-bihar
git pull
sudo systemctl restart hmis-bot

# Edit config
nano ~/ai-conversational-analytics-agent-hmis-bihar/.env
sudo nano /etc/caddy/Caddyfile

# Disk + memory
df -h
free -h
top                    # quick CPU view (q to quit)

# Audit log
tail -10 ~/ai-conversational-analytics-agent-hmis-bihar/data/audit_log.jsonl
wc -l ~/ai-conversational-analytics-agent-hmis-bihar/data/audit_log.jsonl
```

### GCP Console (browser)
- VM list: https://console.cloud.google.com/compute/instances
- IP addresses: https://console.cloud.google.com/networking/addresses/list
- Firewall rules: https://console.cloud.google.com/networking/firewalls/list
- Billing: https://console.cloud.google.com/billing
- BigQuery console: https://console.cloud.google.com/bigquery

### Meta dashboard
- App list: https://developers.facebook.com/apps/
- Business settings (system users): https://business.facebook.com/settings

### OpenAI
- Dashboard: https://platform.openai.com/
- API keys: https://platform.openai.com/api-keys
- Usage: https://platform.openai.com/usage
- Fine-tuning: https://platform.openai.com/finetune

---

## 16. Future improvements & known limitations

### Known limitations
- **Sandbox phone number**: The bot uses Meta's free test number, which restricts messaging to allow-listed users (max 5). To open to all of Bihar's program officers, you need to register a real phone number with Meta and complete business verification.
- **In-memory user state**: The state machine (idle / awaiting clarification / etc.) is stored in `_user_data` dict in `bot.py`. On restart, all in-flight conversations lose their state — users mid-confirmation have to restart their query. Acceptable for low-traffic; could be moved to Redis for multi-instance scaling.
- **Single VM**: No load balancing, no high availability. If the VM crashes (rare), the bot is offline until it restarts. systemd handles process-level crashes; for VM-level crashes, GCP would notify and you'd manually restart from the console.
- **Audit log on local disk**: The `audit_log.jsonl` lives only on the VM. If the VM is destroyed, history is lost (but the Google Sheet is the duplicate). Worth setting up a daily backup to GCS bucket: `gsutil cp data/audit_log.jsonl gs://your-bucket/audit-backups/`.
- **No tests for prompt regressions**: When you tweak the system prompt, there's no automated way to verify the changes don't break existing queries. Workflow is manual: deploy, send test messages, compare responses.

### Suggested improvements (future work)
- **Hindi / Hinglish prompt examples**: Add more example queries in `_HELP_SECTIONS` and the system prompt to bias the model toward Hindi/Hinglish phrasings.
- **Per-user rate limiting**: Currently no rate limit. If a user sends 10 queries in 30 seconds, the bot processes all of them (parallel asyncio). Consider a simple token bucket per user (5 queries/minute).
- **Daily summary via WhatsApp**: A cron job that pushes daily key indicators to a list of users every morning at 9 AM (e.g., "FIC coverage in your district yesterday: 67.8%, down 1.2% from previous week"). Requires Meta-approved template messages.
- **Dashboard view on a web URL**: A simple read-only HTML dashboard at `https://hmis-bh-bot.duckdns.org/dashboard` showing recent queries, top users, common questions. Useful for program managers.
- **Backup automation**: nightly backup of `data/audit_log.jsonl` to a GCS bucket. Survives VM destruction.
- **OpenAI prompt caching audit**: OpenAI's automatic prompt caching kicks in for repeated 8k+-token prefixes. Inspect actual token usage in the OpenAI dashboard to confirm caching is working as expected; this is the largest cost lever.
- **Switch to fine-tuned model**: After ~3 months of production data, do the fine-tuning step ([Section 12](#12-fine-tuning-the-model)) to reduce both latency and cost.

---

## 17. Appendix: build history

Rough timeline of how the project came together:

| Phase | What happened |
|-------|---------------|
| Pre-WhatsApp | Telegram-bot version built, deployed locally, used for ~2 weeks of internal testing |
| Migration | Swapped Telegram → WhatsApp Cloud API. Replaced `python-telegram-bot` with FastAPI + `httpx` for the Meta Graph API. Renamed all "Telegram" references to "WhatsApp" in prompts and docs. |
| Cleanup | Reorganized repo into `tools/`, `tests/`, `docs/`, `data/`. Moved sensitive files out, fixed `.gitignore`. |
| Polish | Added smart `/help` menu (state-machine driven), per-message footnote, plain-English chart labels (no "Section A / B1" codes), aggressive Excel export, ZD calculation rule, average-over-multi-month rule. |
| GitHub | `git init` + first commit + GitHub repo + push (Phase 1 of deployment). |
| Production deploy | GCE VM (e2-small, asia-south1), DuckDNS DNS, Caddy + Let's Encrypt, systemd service, Meta webhook URL update (Phase 2). Production verified via `/help` round-trip from Mohit's personal WhatsApp. |
| Future | Migrate to wjcf.in subdomain when DNS access is granted. Real phone number after Meta business verification. Fine-tune model on curated audit log examples. |

---

*End of project log. Last updated: 2026-05-07.*
