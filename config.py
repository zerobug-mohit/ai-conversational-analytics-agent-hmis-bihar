"""
config.py
---------
Loads all environment variables from the .env file using python-dotenv.
All other modules import from here — credentials are never hardcoded anywhere.
Raises a clear error at startup if any required variable is missing.
"""

import os
from dotenv import load_dotenv

# Load variables from the .env file in the same directory
load_dotenv()

# --- WhatsApp Cloud API (Meta) ---
# Permanent System User access token for the WhatsApp Business Account.
# Get it from Meta for Developers → your app → System users → generate token
# with whatsapp_business_messaging + whatsapp_business_management scopes.
WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")

# Numeric phone-number ID assigned to your test/business WhatsApp number.
# Find it in Meta for Developers → WhatsApp → API Setup.
WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# Token YOU pick — used by Meta to verify your webhook URL during setup.
# Any random string; must match what you enter in Meta's webhook config.
WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

# App secret from Meta — used to validate that incoming webhooks were
# actually sent by Meta (HMAC-SHA256 signature on the request body).
# Optional for local dev; STRONGLY recommended in production.
WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")

# Graph API version — bump when Meta releases new versions.
WHATSAPP_GRAPH_VERSION: str = os.getenv("WHATSAPP_GRAPH_VERSION", "v22.0")

# --- Web server (FastAPI / uvicorn) ---
# 0.0.0.0 binds on all interfaces; needed for Meta's webhook to reach the bot.
SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8080"))

# --- OpenAI ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
# Primary model — used for SQL generation, ambiguity check, relevance gate, key findings.
# Override in .env to use a newer model (e.g. gpt-4.1, o3, gpt-4.1-mini).
AI_MODEL_SQL: str = os.getenv("AI_MODEL_SQL", "gpt-5.4")
# Fast model — used for geo extraction, chart spec, and conversational replies.
AI_MODEL_FAST: str = os.getenv("AI_MODEL_FAST", "gpt-5.4-mini")

# --- Google BigQuery ---
GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
BIGQUERY_PROJECT_ID: str = os.getenv("BIGQUERY_PROJECT_ID", "biharhmisdatafordvctool")

# --- Conversation settings ---
MAX_HISTORY_EXCHANGES: int = int(os.getenv("MAX_HISTORY_EXCHANGES", "5"))

# --- Audit logging ---
# Google Sheet ID for the audit log (the part of the URL between /d/ and /edit).
# Leave blank on first run — the bot will create a sheet and print its ID.
AUDIT_SHEET_ID: str = os.getenv("AUDIT_SHEET_ID", "")


# ---- Startup validation ----
_required = {
    "WHATSAPP_ACCESS_TOKEN": WHATSAPP_ACCESS_TOKEN,
    "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
    "WHATSAPP_VERIFY_TOKEN": WHATSAPP_VERIFY_TOKEN,
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "GOOGLE_APPLICATION_CREDENTIALS": GOOGLE_APPLICATION_CREDENTIALS,
}

for _name, _value in _required.items():
    if not _value:
        raise ValueError(
            f"\n\n[CONFIG ERROR] Missing required environment variable: {_name}\n"
            f"Please open the .env file and fill in all required values.\n"
            f"See .env.example for a template.\n"
        )

# Verify the service account file actually exists on disk
if not os.path.isfile(GOOGLE_APPLICATION_CREDENTIALS):
    raise FileNotFoundError(
        f"\n\n[CONFIG ERROR] BigQuery credentials file not found: {GOOGLE_APPLICATION_CREDENTIALS}\n"
        f"Make sure GOOGLE_APPLICATION_CREDENTIALS in .env points to your service_account.json file.\n"
    )
