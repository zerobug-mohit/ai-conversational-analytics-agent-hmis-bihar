"""
audit_logger.py
---------------
Records every bot interaction to two places:

1. Google Sheets — one row per interaction, human-readable, with a manual
   "✓ Good Example" checkbox column for fine-tuning curation.

2. audit_log.jsonl — newline-delimited JSON, one record per interaction,
   including full SQL and fine-tuning message stubs for offline analysis.

Both writes run in a background thread and never block the bot response.
If either write fails, the error is logged but the bot continues normally.

Google Sheets first-time setup
--------------------------------
1. Enable the Google Sheets API in your Google Cloud Console
   (same project as BigQuery — just search "Sheets API" and click Enable).
2. Leave AUDIT_SHEET_ID blank in .env on first run.
   The bot will create a new sheet, print its ID and URL to the terminal,
   and you can then paste the ID into .env.
3. Open the printed URL and share the sheet with your personal Google account
   so you can view and edit it.
"""

import asyncio
import contextvars
import json
import logging
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
_SHEET_TITLE = "HMIS Bot Audit Log"
_TAB_NAME    = "Interactions"
# Resolve JSONL path relative to this file so it doesn't depend on the
# process CWD (systemd, cron, container entrypoints can all differ).
_JSONL_PATH  = Path(__file__).resolve().parent / "data" / "audit_log.jsonl"

_HEADERS = [
    "Timestamp (UTC)",
    "Interaction ID",
    "User ID",
    "Username",
    "User Message",
    "Geo Entity",
    "Generated SQL",
    "SQL Attempts",
    "BQ Rows Returned",
    "Response Text",
    "Chart Type",
    "Success",
    "Error Message",
    "Processing Time (ms)",
    "✓ Good Example",   # manual — tick to mark for fine-tuning
    "Notes",            # manual — freeform
    "Backend Log",      # auto — captured pipeline log for this interaction
]

_worksheet: gspread.Worksheet | None = None
# Cool-off timestamp after a failed init: we keep retrying so a transient
# auth/network blip at boot doesn't permanently disable Sheets logging for
# the rest of the process's life.
_next_init_attempt_at: float = 0.0
_INIT_RETRY_SECONDS: float = 60.0

# Strong refs to in-flight background write tasks. asyncio.create_task only
# returns a weakly-referenced handle — without this set, the GC can collect
# the task mid-flight and the write silently disappears. Tasks remove
# themselves on completion via the done callback.
_pending_writes: set[asyncio.Task] = set()


# ── Per-interaction log capture ──────────────────────────────────────────────
# Each Telegram message is processed in its own asyncio task. We use a
# contextvars.ContextVar so each task gets its OWN buffer — concurrent users
# don't mix log lines.

_log_buffer: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "audit_log_buffer", default=None
)

# Sheets cell limit is 50_000 chars; truncate well below that to be safe.
_MAX_BACKEND_LOG_CHARS = 45_000


class _CaptureLogHandler(logging.Handler):
    """Logging handler that appends each formatted record to the per-task buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        buf = _log_buffer.get()
        if buf is None:
            return
        try:
            buf.append(self.format(record))
        except Exception:
            # Never let logging crash the bot
            pass


def install_capture_handler() -> None:
    """
    Attach the capture handler to the root logger. Idempotent — safe to call
    multiple times. Should be called once at bot startup, after
    logging.basicConfig().
    """
    root = logging.getLogger()
    if any(isinstance(h, _CaptureLogHandler) for h in root.handlers):
        return
    handler = _CaptureLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)


def start_capture() -> None:
    """Begin capturing log records for the current async task."""
    _log_buffer.set([])


def end_capture() -> str:
    """
    Stop capturing for the current task and return the captured lines as a
    single newline-joined string. Truncates to _MAX_BACKEND_LOG_CHARS.
    """
    buf = _log_buffer.get() or []
    _log_buffer.set(None)
    text = "\n".join(buf)
    if len(text) > _MAX_BACKEND_LOG_CHARS:
        text = text[:_MAX_BACKEND_LOG_CHARS] + "\n... [truncated]"
    return text


# ── Google Sheets initialisation (lazy, once) ────────────────────────────────

def _init_worksheet() -> gspread.Worksheet | None:
    global _worksheet, _next_init_attempt_at
    if _worksheet is not None:
        return _worksheet
    # Back off briefly after failures so we don't hammer the Sheets API on
    # every webhook, but DO keep retrying — a one-time hiccup at boot used
    # to disable logging for the lifetime of the process.
    if time.monotonic() < _next_init_attempt_at:
        return None

    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    sheet_id   = os.getenv("AUDIT_SHEET_ID", "").strip()

    try:
        creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
        gc    = gspread.authorize(creds)

        if sheet_id:
            sh = gc.open_by_key(sheet_id)
        else:
            sh = gc.create(_SHEET_TITLE)
            logger.warning(
                "\n"
                "┌─ AUDIT SHEET CREATED ────────────────────────────────────────┐\n"
                f"│  Title : {_SHEET_TITLE:<52}│\n"
                f"│  ID    : {sh.id:<52}│\n"
                f"│  URL   : {sh.url:<52}│\n"
                "│                                                              │\n"
                "│  → Add  AUDIT_SHEET_ID=" + sh.id + "  to your .env         │\n"
                "│  → Share the sheet with your personal Google account         │\n"
                "└──────────────────────────────────────────────────────────────┘"
            )

        # Get or create the Interactions tab
        try:
            ws = sh.worksheet(_TAB_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(_TAB_NAME, rows=10000, cols=len(_HEADERS))
            ws.append_row(_HEADERS, value_input_option="USER_ENTERED")
            # Bold the header row
            ws.format("A1:Q1", {"textFormat": {"bold": True}})
            # Freeze the header row
            sh.batch_update({"requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": ws.id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            }]})

        _worksheet = ws
        logger.info(
            f"Google Sheets audit log initialised "
            f"(sheet_id={sheet_id or sh.id}, tab={_TAB_NAME})."
        )
        return _worksheet

    except Exception as exc:
        _next_init_attempt_at = time.monotonic() + _INIT_RETRY_SECONDS
        # Log the full traceback once on each retry so the operator sees the
        # actual cause (wrong sheet id, service account not shared, missing
        # Sheets API, etc.) instead of just a one-line summary.
        logger.error(
            "Google Sheets init failed — JSONL will still capture writes. "
            f"Retry in {_INIT_RETRY_SECONDS:.0f}s. Cause: {exc!r}\n"
            f"{traceback.format_exc()}"
        )
        return None


def warmup() -> None:
    """
    Eagerly attempt Sheets init at bot startup so misconfigurations
    (missing AUDIT_SHEET_ID, service account not shared with the sheet,
    Sheets API not enabled) surface in the boot log instead of being
    hidden inside the first message's background task.
    """
    _init_worksheet()


# ── Synchronous write (runs in thread pool) ──────────────────────────────────

def _write_sync(record: dict) -> None:
    """Write one interaction record to JSONL and Google Sheets."""

    # ---- 1. JSONL ----
    try:
        _JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.error(f"JSONL write failed: {exc}")

    # ---- 2. Google Sheets ----
    ws = _init_worksheet()
    if ws is None:
        return
    try:
        ws.append_row(
            [
                record["timestamp"],
                record["id"],
                record["user_id"],
                record["username"] or "",
                record["user_message"],
                json.dumps(record["geo_entity"], ensure_ascii=False)
                    if record["geo_entity"] else "",
                record["generated_sql"] or "",
                record["sql_attempts"],
                record["bq_row_count"] if record["bq_row_count"] is not None else "",
                record["response_text"],
                record["chart_type"] or "",
                "TRUE" if record["success"] else "FALSE",
                record["error_message"] or "",
                record["processing_time_ms"],
                "",   # ✓ Good Example — filled manually
                "",   # Notes — filled manually
                record.get("backend_log") or "",
            ],
            value_input_option="USER_ENTERED",
        )
    except Exception as exc:
        # Include traceback so 403 (sheet not shared), 404 (bad ID), and
        # quota errors are distinguishable in the logs.
        logger.error(
            f"Google Sheets append failed: {exc!r}\n{traceback.format_exc()}"
        )


# ── Public async entry point ─────────────────────────────────────────────────

async def log_interaction(
    *,
    user_id: int,
    username: str | None,
    user_message: str,
    geo_entity: dict | None,
    generated_sql: str | None,
    sql_attempts: int,
    bq_row_count: int | None,
    response_text: str,
    chart_type: str | None,
    success: bool,
    error_message: str | None,
    processing_time_ms: int,
    backend_log: str = "",
) -> None:
    """
    Fire-and-forget async logger.
    Builds the record, writes to JSONL and Sheets in a background thread.
    Returns immediately — never blocks the caller.
    """
    record = {
        "id":                  str(uuid.uuid4())[:8],
        "timestamp":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "user_id":             user_id,
        "username":            username,
        "user_message":        user_message,
        "geo_entity":          geo_entity,
        "generated_sql":       generated_sql,
        "sql_attempts":        sql_attempts,
        "bq_row_count":        bq_row_count,
        "response_text":       response_text,
        "chart_type":          chart_type,
        "success":             success,
        "error_message":       error_message,
        "processing_time_ms":  processing_time_ms,
        "backend_log":         backend_log,
        # Fine-tuning stubs — enough to reconstruct training examples offline
        "finetuning": {
            "sql_generation": {
                "input":  f"Question: {user_message}\nGeo: {json.dumps(geo_entity, default=str)}",
                "output": generated_sql or "",
            },
            "response_generation": {
                "input":  user_message,
                "output": response_text,
            },
        },
    }

    # Schedule background write — create_task returns immediately.
    # IMPORTANT: keep a strong reference in _pending_writes. Python's event
    # loop only weakly references tasks, so without this set the GC can
    # collect the task mid-flight and the row is silently lost.
    task = asyncio.create_task(asyncio.to_thread(_write_sync, record))
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)
