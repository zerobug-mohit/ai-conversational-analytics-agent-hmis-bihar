"""
bot.py
------
Entry point for the HMIS WhatsApp analytics bot.

WhatsApp Cloud API uses webhooks (Meta POSTs each inbound message to our
public HTTPS endpoint), not long polling. So this module is a small FastAPI
application that:

  1. Verifies the webhook URL on first setup (GET /webhook handshake).
  2. Receives inbound messages on POST /webhook, validates the Meta
     signature, dedupes, and dispatches each message to a background task.
  3. Background tasks run the same per-user state machine as before:
        idle → awaiting_clarification → awaiting_confirmation → ...
     and call back into the WhatsApp Cloud API to send the response.

The pipeline (geo extract → BigQuery lookup → SQL gen → run → summarise →
chart → exports) is identical to the previous Telegram version. Only the
chat-platform glue at the edges changed.

Run with:
    python bot.py
"""

import asyncio
import datetime
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn

import audit_logger
import bigquery_client
import chart as chart_module
import claude_client
import export_utils
import schema_cache
import whatsapp_client
from config import (
    MAX_HISTORY_EXCHANGES,
    SERVER_HOST,
    SERVER_PORT,
    WHATSAPP_VERIFY_TOKEN,
)

# Messages older than this on arrival are silently dropped — they accumulated
# while the bot was offline and would confuse users if processed late.
_MAX_MESSAGE_AGE_SECONDS = 60

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Standard error message shown to users whenever the pipeline fails
# ---------------------------------------------------------------------------
_ERR = (
    "The conversational analytics AI agent is facing an error. "
    "Please try again in a moment. "
    "If the problem persists, contact the admin at mchaurasiya@wjcf.in"
)

# ---------------------------------------------------------------------------
# Per-user state (in-memory; resets when the bot restarts)
# ---------------------------------------------------------------------------
# Keyed by WhatsApp phone-number string (e.g. "919876543210").
_user_data: dict[str, dict] = {}

# Per-user asyncio lock — ensures only one message per user is processed at a
# time.  Without this, rapid follow-up messages (or Meta webhook retries) can
# interleave at await points and corrupt conversation state.
_user_locks: dict[str, asyncio.Lock] = {}

# Bounded set of recently-processed message IDs — Meta sometimes retries
# webhooks; this prevents double-processing.
_processed_message_ids: set[str] = set()
_PROCESSED_MAX = 1000


def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Return (creating if needed) the per-user asyncio.Lock."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _get_state(user_id: str) -> dict:
    if user_id not in _user_data:
        _user_data[user_id] = {
            "history": [],
            "state": "idle",
            "pending": {},
            "_last_seen": time.time(),
        }
    else:
        _user_data[user_id]["_last_seen"] = time.time()
    return _user_data[user_id]


def _add_to_history(user_id: str, role: str, content: str) -> None:
    state = _get_state(user_id)
    state["history"].append({"role": role, "content": content})
    max_messages = MAX_HISTORY_EXCHANGES * 2
    if len(state["history"]) > max_messages:
        state["history"] = state["history"][-max_messages:]


def _reset_pending(user_id: str) -> None:
    state = _get_state(user_id)
    state["state"] = "idle"
    state["pending"] = {}


# ---------------------------------------------------------------------------
# Geo disambiguation helpers
# ---------------------------------------------------------------------------

def _build_clarification_message(
    matches: list[dict],
    geo_type: str,
    user_input_name: str,
) -> str:
    lines = [
        f"I found {len(matches)} places matching *{user_input_name}* — "
        f"which one did you mean?\n"
    ]
    for i, row in enumerate(matches, 1):
        if geo_type == "district":
            label = f"{row.get('district_name')}  (code: {row.get('district_code')})"
        elif geo_type == "block":
            label = (
                f"{row.get('sub_district_ulb_name')}  —  "
                f"{row.get('district_name')} district  "
                f"(code: {row.get('sub_district_ulb_code')})"
            )
        elif geo_type == "facility":
            label = (
                f"{row.get('facility_name')}  —  "
                f"{row.get('sub_district_ulb_name', row.get('block_name'))} block, "
                f"{row.get('district_name')} district  "
                f"(code: {row.get('facility_code')})"
            )
        else:
            label = str(row)
        lines.append(f"{i}. {label}")

    lines.append(
        "\nPlease reply with the number (e.g. *1*) or the district/block name."
    )
    return "\n".join(lines)


def _resolve_clarification(user_reply: str, matches: list[dict], geo_type: str) -> dict | None:
    reply = user_reply.strip()
    if reply.isdigit():
        idx = int(reply) - 1
        if 0 <= idx < len(matches):
            return matches[idx]
        return None

    reply_lower = reply.lower()
    name_fields = {
        "district": ["district_name"],
        "block":    ["sub_district_ulb_name", "district_name"],
        "facility": ["facility_name", "sub_district_ulb_name", "district_name"],
        "state":    ["state"],
    }.get(geo_type, [])

    for row in matches:
        for field in name_fields:
            value = str(row.get(field, "")).lower()
            if reply_lower in value or value in reply_lower:
                return row

    return None


# ---------------------------------------------------------------------------
# Core query pipeline (called after geo is confirmed) — identical logic
# ---------------------------------------------------------------------------

async def _run_query_pipeline(
    user_id: str,
    original_question: str,
    confirmed_entities: list[dict],
    trace: dict,
    geo_type: str | None = None,
) -> tuple[str, object]:
    state = _get_state(user_id)
    history = state["history"]

    logger.info(f"Generating SQL for user {user_id}. Entities: {confirmed_entities}")
    sql = await claude_client.generate_sql(
        user_question=original_question,
        confirmed_entities=confirmed_entities,
        history=history,
    )

    trace["generated_sql"] = sql

    if sql.upper().startswith("CANNOT_GENERATE:"):
        reason = sql[len("CANNOT_GENERATE:"):].strip()
        logger.info(f"SQL not needed for this question ({reason}). Trying conversational reply.")
        reply = await claude_client.answer_conversational(original_question, history)
        trace["success"] = True
        return reply, None

    logger.info(f"Running BigQuery query for user {user_id}")
    results = None
    last_error_str = None
    max_attempts = 3
    for attempt in range(max_attempts):
        trace["sql_attempts"] = attempt + 1
        try:
            results = await asyncio.to_thread(bigquery_client.run_query, sql)
            break

        except ValueError as exc:
            logger.error(f"Unsafe SQL blocked for user {user_id}: {exc}")
            trace["error_message"] = str(exc)
            return _ERR, None

        except RuntimeError as exc:
            error_str = str(exc)
            fixable = "400" in error_str
            if fixable and attempt < max_attempts - 1:
                logger.warning(
                    f"BigQuery 400 on attempt {attempt + 1} for user {user_id}. "
                    f"Asking model to fix SQL. Error: {error_str}"
                )
                sql = await claude_client.fix_sql(
                    broken_sql=sql,
                    bq_error=error_str,
                    history=history,
                )
                trace["generated_sql"] = sql
                if sql.upper().startswith("CANNOT_GENERATE:"):
                    reason = sql[len("CANNOT_GENERATE:"):].strip()
                    trace["error_message"] = reason
                    return _ERR, None
                logger.info(f"Retrying with fixed SQL (attempt {attempt + 2})")
                continue

            logger.error(f"BigQuery error for user {user_id}: {exc}")
            last_error_str = error_str
            trace["error_message"] = error_str
            break

    if results is None and last_error_str is not None:
        logger.info(f"SQL auto-fix exhausted for user {user_id}. Diagnosing failure.")
        diagnosis = await claude_client.diagnose_sql_failure(
            broken_sql=sql,
            bq_error=last_error_str,
            original_question=original_question,
            history=history,
        )
        if diagnosis.get("needs_user_clarification"):
            clarification_q = diagnosis.get(
                "clarification_question",
                "I couldn't complete your query. Could you clarify what you meant?"
            )
            user_state = _get_state(user_id)
            user_state["state"] = "awaiting_sql_clarification"
            user_state["pending"] = {
                "original_question": original_question,
                "confirmed_entities": confirmed_entities,
                "geo_type": geo_type,
            }
            logger.info(f"Asking user for SQL clarification: {clarification_q!r}")
            return clarification_q, None
        return _ERR, None

    if not results:
        return (
            "No data was found for that query. "
            "The time period or location may have no records in the database. "
            "Try a different month range or check the location name."
        ), None

    trace["bq_row_count"] = len(results)
    trace["bq_rows"]      = results

    logger.info(f"Summarizing {len(results)} rows for user {user_id}")
    answer_and_meta, key_findings, chart_spec = await asyncio.gather(
        claude_client.summarize_results(
            user_question=original_question,
            results=results,
            history=history,
            generated_sql=sql,
        ),
        claude_client.generate_key_findings(
            user_question=original_question,
            results=results,
            history=history,
        ),
        claude_client.get_chart_spec(
            user_question=original_question,
            results=results,
            history=history,
        ),
    )

    _AGENT_DID_HEADER_NEW = "*What the agent did*"
    _AGENT_DID_HEADER_OLD = "**What the agent did**"
    _split_on = (
        _AGENT_DID_HEADER_NEW if _AGENT_DID_HEADER_NEW in answer_and_meta
        else _AGENT_DID_HEADER_OLD if _AGENT_DID_HEADER_OLD in answer_and_meta
        else None
    )
    if _split_on:
        before, after = answer_and_meta.split(_split_on, 1)
        summary = (
            before.rstrip()
            + "\n\n" + key_findings
            + "\n\n" + _split_on + after
        )
    else:
        summary = answer_and_meta + "\n\n" + key_findings

    trace["export_title"] = (
        chart_spec.get("title") or original_question[:60]
    ).strip()
    trace["should_export_excel"] = chart_spec.get("should_export_excel", True)
    trace["chart_spec"]         = chart_spec

    chart_buf = await asyncio.to_thread(chart_module.generate_chart, chart_spec, results)
    if chart_buf:
        chart_type = chart_spec.get("chart_type")
        logger.info(f"Chart generated for user {user_id} ({chart_type})")
        trace["chart_type"] = chart_type

    trace["success"] = True
    return summary, chart_buf


# ---------------------------------------------------------------------------
# Understanding confirmation
# ---------------------------------------------------------------------------

async def _go_to_confirmation(
    user_id: str,
    original_question: str,
    confirmed_entities: list[dict],
    trace: dict,
    geo_type: str | None = None,
) -> tuple[str, object]:
    state = _get_state(user_id)
    history = state["history"]

    understanding = await claude_client.formulate_understanding(
        user_question=original_question,
        confirmed_entities=confirmed_entities,
        history=history,
    )

    state["state"] = "awaiting_confirmation"
    state["pending"] = {
        "original_question": original_question,
        "confirmed_entities": confirmed_entities,
        "geo_type": geo_type,
    }
    logger.info(f"Awaiting confirmation from user {user_id}")
    return understanding, None


# ---------------------------------------------------------------------------
# State-machine handlers
# ---------------------------------------------------------------------------

async def _handle_clarification(user_id: str, reply_text: str, trace: dict) -> tuple[str, object]:
    state = _get_state(user_id)
    pending = state["pending"]
    matches = pending["matches"]
    geo_type = pending["geo_type"]
    original_question = pending["original_question"]
    confirmed_entities = list(pending.get("confirmed_entities", []))
    remaining_entities = list(pending.get("remaining_entities", []))

    if reply_text.lower() in {"cancel", "nevermind", "never mind", "stop", "quit", "exit"}:
        _reset_pending(user_id)
        return "OK, I've cancelled that query. You can ask me a new question.", None

    confirmed = _resolve_clarification(reply_text, matches, geo_type)

    if confirmed is None:
        return (
            "I couldn't match your reply to any of the options. "
            "Please reply with the option number (e.g. *1*, *2*, ...) "
            "or type *cancel* to start over."
        ), None

    confirmed_entities.append(confirmed)
    logger.info(
        f"User {user_id} clarification resolved: {confirmed}. "
        f"Remaining entities to clarify: {len(remaining_entities)}"
    )

    if remaining_entities:
        next_entity = remaining_entities[0]
        rest = remaining_entities[1:]
        state["state"] = "awaiting_clarification"
        state["pending"] = {
            "original_question": original_question,
            "geo_type": next_entity["geo_type"],
            "user_input_name": next_entity["user_input_name"],
            "matches": next_entity["matches"],
            "confirmed_entities": confirmed_entities,
            "remaining_entities": rest,
        }
        return _build_clarification_message(
            next_entity["matches"], next_entity["geo_type"], next_entity["user_input_name"]
        ), None

    _reset_pending(user_id)

    return await _go_to_confirmation(
        user_id=user_id,
        original_question=original_question,
        confirmed_entities=confirmed_entities,
        trace=trace,
        geo_type=geo_type,
    )


async def _handle_confirmation(
    user_id: str, reply_text: str, trace: dict
) -> tuple[str, object]:
    state = _get_state(user_id)
    pending = state["pending"]
    original_question = pending["original_question"]
    confirmed_entities = pending.get("confirmed_entities", [])
    geo_type = pending.get("geo_type")
    history = state["history"]

    if reply_text.lower() in {"cancel", "nevermind", "never mind", "stop", "quit", "exit"}:
        _reset_pending(user_id)
        return "OK, I've cancelled that query. You can ask me a new question.", None

    confirmation = await claude_client.check_if_confirmed(reply_text, history)

    if confirmation.get("confirmed", True):
        _reset_pending(user_id)
        final_query = await claude_client.synthesize_final_query(history, confirmed_entities)
        return await _run_query_pipeline(
            user_id=user_id,
            original_question=final_query,
            confirmed_entities=confirmed_entities,
            trace=trace,
            geo_type=geo_type,
        )
    else:
        augmented_question = f"{original_question}\n[User clarified: {reply_text}]"

        updated_entities = confirmed_entities
        try:
            geo_info = await claude_client.extract_geo_entity(reply_text, history)
            if geo_info.get("has_geo") and geo_info.get("entities"):
                lookup_results = await asyncio.gather(
                    *[asyncio.to_thread(bigquery_client.run_query, e["lookup_sql"])
                      for e in geo_info["entities"]],
                    return_exceptions=True,
                )
                new_confirmed: list[dict] = []
                needs_disambiguation: list[dict] = []
                for entity, result in zip(geo_info["entities"], lookup_results):
                    if isinstance(result, Exception):
                        continue
                    if len(result) == 1:
                        new_confirmed.append(result[0])
                    elif len(result) > 1:
                        needs_disambiguation.append({
                            "geo_type": entity["geo_type"],
                            "user_input_name": entity["user_input_name"],
                            "matches": result,
                        })

                if needs_disambiguation:
                    first = needs_disambiguation[0]
                    remaining = needs_disambiguation[1:]
                    state["state"] = "awaiting_clarification"
                    state["pending"] = {
                        "original_question": augmented_question,
                        "geo_type": first["geo_type"],
                        "user_input_name": first["user_input_name"],
                        "matches": first["matches"],
                        "confirmed_entities": new_confirmed,
                        "remaining_entities": remaining,
                    }
                    return _build_clarification_message(
                        first["matches"], first["geo_type"], first["user_input_name"]
                    ), None

                if new_confirmed:
                    updated_entities = new_confirmed
                    logger.info(f"User {user_id} geo updated to: {updated_entities}")
        except Exception as exc:
            logger.warning(f"Geo re-extraction during correction failed for user {user_id}: {exc}")

        pending["original_question"] = augmented_question
        pending["confirmed_entities"] = updated_entities
        state["pending"] = pending

        understanding = await claude_client.formulate_understanding(
            user_question=augmented_question,
            confirmed_entities=updated_entities,
            history=history,
        )
        logger.info(f"User {user_id} corrected understanding — re-confirming")
        return understanding, None


async def _handle_help_choice(
    user_id: str, reply_text: str, trace: dict
) -> tuple[str, object]:
    """
    User is in the awaiting_help_choice state. Resolve their reply to a help
    section, re-show the menu, exit, or fall through to a normal query.
    """
    text = reply_text.strip().lower()

    # Exit phrases — leave the menu without sending a section
    if text in {"cancel", "exit", "back", "done", "stop", "quit", "no", "no thanks"}:
        _reset_pending(user_id)
        trace["success"] = True
        trace["is_help_response"] = True
        return ("OK. Ask me a question any time, or type /help to see the "
                "menu again."), None

    # Re-show the menu (stay in the help state)
    if text in {"menu", "show menu", "options", "list", "/help", "help"}:
        trace["is_help_response"] = True
        return _HELP_MENU, None

    section_id = _match_help_section(reply_text)
    if section_id is not None:
        # Sending a section auto-exits the menu — user can re-enter with /help
        _reset_pending(user_id)
        trace["success"] = True
        trace["is_help_response"] = True
        return _HELP_SECTIONS[section_id] + _HELP_FOOTER, None

    # No menu match — assume the user has moved on with a real question.
    _reset_pending(user_id)
    return await _handle_new_question(user_id, reply_text, trace)


async def _handle_sql_clarification(
    user_id: str, reply_text: str, trace: dict
) -> tuple[str, object]:
    state = _get_state(user_id)
    pending = state["pending"]
    original_question = pending["original_question"]
    confirmed_entities = pending.get("confirmed_entities", [])
    geo_type = pending["geo_type"]

    if reply_text.lower() in {"cancel", "nevermind", "never mind", "stop", "quit", "exit"}:
        _reset_pending(user_id)
        return "OK, I've cancelled that query. You can ask me a new question.", None

    augmented_question = f"{original_question}\n[User clarified for query fix: {reply_text}]"
    _reset_pending(user_id)

    return await _run_query_pipeline(
        user_id=user_id,
        original_question=augmented_question,
        confirmed_entities=confirmed_entities,
        trace=trace,
        geo_type=geo_type,
    )


async def _handle_new_question(user_id: str, message_text: str, trace: dict) -> tuple[str, object]:
    state = _get_state(user_id)
    history = state["history"]

    logger.info(f"Checking relevance for user {user_id}: {message_text!r}")
    try:
        relevance = await claude_client.check_query_relevance(message_text, history)
    except Exception as exc:
        logger.warning(f"Relevance check failed ({exc}) — proceeding anyway")
        relevance = {"is_relevant": True}

    if not relevance.get("is_relevant", True):
        trace["success"] = True
        return relevance.get("reply", "I can only answer questions about HMIS health data."), None

    if not relevance.get("needs_sql", True):
        logger.info(f"No SQL needed for user {user_id} — answering conversationally")
        reply = await claude_client.answer_conversational(message_text, history)
        trace["success"] = True
        return reply, None

    logger.info(f"Extracting geo entity for user {user_id}: {message_text!r}")
    try:
        geo_info = await claude_client.extract_geo_entity(message_text, history)
    except Exception as exc:
        logger.error(f"Geo extraction failed: {exc}", exc_info=True)
        return _ERR, None

    logger.info(f"Geo info: {geo_info}")

    if not geo_info.get("has_geo"):
        logger.info(f"No new geo entity — relying on conversation history for user {user_id}")
        return await _go_to_confirmation(
            user_id=user_id,
            original_question=message_text,
            confirmed_entities=[],
            trace=trace,
            geo_type=None,
        )

    entities = geo_info.get("entities", [])
    if not entities:
        logger.info(f"has_geo=true but empty entities for user {user_id} — treating as no geo")
        return await _go_to_confirmation(
            user_id=user_id,
            original_question=message_text,
            confirmed_entities=[],
            trace=trace,
            geo_type=None,
        )

    try:
        lookup_results = await asyncio.gather(
            *[asyncio.to_thread(bigquery_client.run_query, e["lookup_sql"]) for e in entities],
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error(f"Geo lookup gather failed: {exc}", exc_info=True)
        return _ERR, None

    confirmed_entities: list[dict] = []
    entities_needing_clarification: list[dict] = []
    geo_type_first = entities[0]["geo_type"] if entities else None

    for entity, result in zip(entities, lookup_results):
        user_input_name = entity["user_input_name"]
        geo_type_e = entity["geo_type"]

        if isinstance(result, Exception):
            logger.error(f"Geo lookup for '{user_input_name}' failed: {result}", exc_info=True)
            return _ERR, None

        matches = result
        if len(matches) == 0:
            return (
                f"I couldn't find *{user_input_name}* in the database.\n"
                "Please check the spelling, or try a partial name "
                "(e.g. 'Begus' instead of 'Begusarai')."
            ), None
        elif len(matches) == 1:
            logger.info(f"Unique geo match for '{user_input_name}' (user {user_id}): {matches[0]}")
            confirmed_entities.append(matches[0])
        else:
            logger.info(f"{len(matches)} geo matches for '{user_input_name}' (user {user_id})")
            entities_needing_clarification.append({
                "geo_type": geo_type_e,
                "user_input_name": user_input_name,
                "matches": matches,
            })

    trace["geo_entity"] = confirmed_entities[0] if confirmed_entities else None

    if not entities_needing_clarification:
        return await _go_to_confirmation(
            user_id=user_id,
            original_question=message_text,
            confirmed_entities=confirmed_entities,
            trace=trace,
            geo_type=geo_type_first,
        )

    first = entities_needing_clarification[0]
    remaining = entities_needing_clarification[1:]
    state["state"] = "awaiting_clarification"
    state["pending"] = {
        "original_question": message_text,
        "geo_type": first["geo_type"],
        "user_input_name": first["user_input_name"],
        "matches": first["matches"],
        "confirmed_entities": confirmed_entities,
        "remaining_entities": remaining,
    }
    return _build_clarification_message(first["matches"], first["geo_type"], first["user_input_name"]), None


# ---------------------------------------------------------------------------
# Help text & "command" handling
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Help system — menu + topic sections
# ---------------------------------------------------------------------------
# /help and /start show a SHORT menu and put the user in awaiting_help_choice
# state. The user replies with a number (1-8) or a short topic keyword to see
# that section's detailed content. Anything else exits the menu and is
# treated as a normal query.

_HELP_MENU = (
    "*Bihar HMIS Analytics Assistant — Help Menu*\n"
    "_Your AI analyst for Bihar's immunization and maternal-health data_\n\n"

    "Reply with a *number* (e.g. _4_) or a short *keyword* "
    "(e.g. _completeness_) to see that section. Or just ask a question any "
    "time — that exits the menu.\n\n"

    "*1. What you can ask* — query examples by topic\n"
    "*2. Data quality* — what checks the bot can run\n"
    "*3. How coverage is calculated* — formula and denominator rules\n"
    "*4. Data completeness methodology* — the 50-field, 4-section check\n"
    "*5. Data correctness methodology* — the 4 logical rules\n"
    "*6. Probable Reporting Errors methodology* — the 16-check, 5-group analysis\n"
    "*7. How to use* — voice, text, follow-ups, exports\n"
    "*8. Tips for better answers* — phrasing your questions well\n\n"

    "Type /clear to reset the conversation.\n\n"
    "_For additional support, please reach out to the developer at_ "
    "mchaurasiya@wjcf.in"
)

# Footer appended to every section — guides the user back to the menu.
_HELP_FOOTER = (
    "\n\n_Type /help to return to the menu, or just ask a new question._"
)

# Footnote appended to every NON-help message (queries, conversational replies,
# clarifications, errors, etc.). Suppressed for help menu / sections / exit
# acks via the trace["is_help_response"] flag.
_FOOTNOTE = (
    "\n\n_Type /help to know more about the capabilities of this Analytics Agent._"
)

_HELP_SECTIONS: dict[str, str] = {
    "1": (
        "*1. What you can ask*\n\n"

        "*Coverage & Immunisation*\n"
        "Coverage % for any antigen at any geographic level.\n"
        "• _What is FIC coverage in Gaya district for FY 2024-25?_\n"
        "• _Compare Pentavalent 3 coverage across all blocks in Darbhanga_\n"
        "• _BCG coverage at facility code 304569 in March 2026_\n"
        "• _Average MR1 coverage in Patna over Q1 FY 2024-25_\n\n"

        "*Trends, Rankings & Comparisons*\n"
        "Month-on-month, year-on-year, top/bottom performers.\n"
        "• _Show BCG coverage trend in Muzaffarpur from Apr to Dec 2024_\n"
        "• _Top 10 districts in Bihar by FIC coverage in March 2026_\n"
        "• _Lowest performing block in Patna for Pentavalent 3 last quarter_\n"
        "• _Compare MR coverage between aspirational districts in FY 2024-25_\n\n"

        "*ANC, Deliveries & Maternal Health*\n"
        "Pregnancy registrations, ANC visits, deliveries, anaemia, GDM.\n"
        "• _1st-trimester ANC registration rate in Begusarai in Q1 FY 2024-25_\n"
        "• _% pregnant women treated for severe anaemia in Patna last year_\n"
        "• _Institutional delivery rate by block in Sitamarhi_\n"
        "• _High-risk pregnancies referred from Darbhanga in March 2026_\n\n"

        "*Newborn & Child Care*\n"
        "Birth weight, breastfeeding, RBSK screening, HBNC visits.\n"
        "• _Low birth weight rate in Saran district in FY 2024-25_\n"
        "• _% newborns breastfed within 1 hour in Vaishali_\n"
        "• _Children given Vitamin A1 in Aurangabad in March 2026_\n\n"

        "*Session Performance*\n"
        "Sessions planned vs held, ASHA presence rates.\n"
        "• _Session dropout rate in Patna in March 2026_\n"
        "• _% sessions with ASHA present in Gaya last quarter_"
    ),

    "2": (
        "*2. Data Quality*\n\n"
        "Three checks — completeness, correctness, and probable reporting "
        "errors. Ask _\"data quality\"_ to run all three at once.\n\n"
        "*Data Completeness*\n"
        "Are facilities reporting all the key fields?\n"
        "• _Show data completeness for Arwal district in March 2026_\n"
        "• _Which facilities in Sadar block (Patna) have incomplete reporting?_\n\n"
        "*Data Correctness*\n"
        "Are reported values logically consistent?\n"
        "• _Data correctness for Saran district in March 2026_\n"
        "• _Which facilities in Begusarai have logical reporting errors?_\n\n"
        "*Probable Reporting Errors (PREs)*\n"
        "Do co-administered vaccines have matching counts?\n"
        "• _PRE rate in Darbhanga in March 2026_\n"
        "• _PRE trend for Sadar block, Patna over the last 6 months_\n"
        "• _% facilities in Gaya with PRE > 10%_\n\n"
        "Type *4*, *5*, or *6* (or _completeness_, _correctness_, _pre_) "
        "for the methodology of each."
    ),

    "3": (
        "*3. How Coverage is Calculated*\n\n"
        "*Formula*\n"
        "Coverage % = SUM(doses given) / (Target × number of months) × 100\n\n"
        "The numerator is the count of vaccine doses, ANC visits, deliveries, "
        "etc. for the period and geography. The denominator is *estimated "
        "infants* for child immunisation, or *estimated pregnancies* for ANC "
        "indicators — both come from population estimates baked into the HMIS "
        "database (annual figures).\n\n"
        "*Target by query level*\n"
        "• Facility-level question → facility's annual target\n"
        "• Block-level question → block's annual target\n"
        "• District-level question → district's annual target\n"
        "• State-level question → state's annual target\n\n"
        "*Multi-month periods*\n"
        "The target is multiplied by the number of months in scope. So "
        "coverage over a quarter compares 3 months of doses against 3 monthly "
        "targets; a full year compares 12 months of doses against the annual "
        "target.\n\n"
        "*If you say \"average\"*\n"
        "Over a multi-month range, the bot calculates each month's coverage "
        "separately and averages those values, instead of the cumulative "
        "formula above. Example: _\"average BCG coverage in Patna from Jan to "
        "Mar 2026\"_ computes Jan-26 %, Feb-26 %, Mar-26 % and averages those "
        "three.\n\n"
        "*Old + new column versions*\n"
        "HMIS recently changed several column names. The bot automatically "
        "uses both old and new versions (using COALESCE) so you don't get "
        "incomplete results when a query crosses format-change months."
    ),

    "4": (
        "*4. Data Completeness — Methodology*\n\n"
        "Each facility-month record is checked against *50 critical fields* "
        "in 4 sections. A record is *fully complete* when all 50 fields are "
        "reported (non-null). The bot reports overall completeness % plus a "
        "per-section breakdown so you can see which area has the most gaps.\n\n"
        "*ANC, Deliveries & Live Births (6 fields)*\n"
        "TD1, TD2, TD Booster, Institutional Deliveries, Live Male Births, "
        "Live Female Births.\n\n"
        "*Birth-to-12-month immunisation (23 fields)*\n"
        "BCG, HepB birth dose, OPV0, Pentavalent 1/2/3, OPV 1/2/3, IPV 1/2/3, "
        "Rotavirus 1/2/3, PCV 1/2, MR1 (9-month), PCV Booster, FIC Male, FIC "
        "Female, Vitamin A1, JE1.\n\n"
        "*Booster & older-child doses (15 fields)*\n"
        "Delayed MR1, Delayed DPT 1/2/3, DPT Booster (2yr), OPV Booster (2yr), "
        "MR2 (16m), DPT Booster 1, OPV Booster, DPT 2nd Booster (5yr), TD10, "
        "TD16, Delayed JE1, JE Booster (2yr), JE2 (16m).\n\n"
        "*AEFI & Session reporting (6 fields)*\n"
        "AEFI Minor, AEFI Severe, AEFI Serious, AEFI Deaths, Sessions Planned, "
        "Sessions Held."
    ),

    "5": (
        "*5. Data Correctness — Methodology*\n\n"
        "A record is *correct* when 4 logical rules hold. A record fails a "
        "rule only when both compared values are reported (non-null) AND the "
        "rule is broken. The bot reports % of facilities passing all 4 rules "
        "plus per-rule pass rates. Rules below 95% suggest systematic "
        "reporting errors worth investigating.\n\n"
        "*Rule A — HepB0 vs Deliveries*\n"
        "HepB0 birth doses cannot exceed institutional deliveries.\n\n"
        "*Rule B — Sessions Held vs Planned*\n"
        "Sessions held cannot exceed sessions planned.\n\n"
        "*Rule C — FIC vs MR1*\n"
        "Fully immunised children cannot exceed MR1 doses (since MR1 is "
        "required for FIC status).\n\n"
        "*Rule D — AEFI Deaths vs Serious cases*\n"
        "AEFI deaths cannot exceed total serious AEFI cases (deaths are a "
        "subset of serious)."
    ),

    "6": (
        "*6. Probable Reporting Errors (PREs) — Methodology*\n\n"
        "PREs flag mismatched counts among vaccines that are co-administered "
        "(given together at the same visit) — these counts SHOULD be "
        "identical, so any difference indicates a reporting discrepancy. "
        "Each facility-month record is checked against *16 PRE rules* across "
        "5 groups.\n\n"
        "*6-week visit (Group A1 — 4 checks)*\n"
        "Pentavalent 1 should equal: OPV1, IPV1, Rotavirus 1, PCV1.\n\n"
        "*10-week visit (Group A2 — 2 checks)*\n"
        "Pentavalent 2 should equal: OPV2, Rotavirus 2.\n\n"
        "*14-week visit (Group A3 — 4 checks)*\n"
        "Pentavalent 3 should equal: OPV3, IPV2, Rotavirus 3, PCV2.\n\n"
        "*9–11 month visit (Group A4 — 3 checks)*\n"
        "MR1 should equal: IPV3, PCV Booster, JE1.\n\n"
        "*Logical impossibility checks (Group B — 3 checks)*\n"
        "• Pentavalent 3 count cannot exceed Pentavalent 1 (more 3rd doses "
        "than 1st doses is impossible)\n"
        "• MR2 count cannot exceed MR1\n"
        "• Zero AEFI minor cases reported (suspicious — minor reactions like "
        "fever or pain are very common after immunisation)\n\n"
        "Each check fires only when both compared values are reported AND "
        "the rule is broken. The bot reports the overall PRE rate (% of all "
        "16 checks fired across records), per-group rates, and the % of "
        "facilities with zero PREs (the cleanest reporters).\n\n"
        "*Note:* PREs indicate reporting discrepancies, not necessarily "
        "real-world clinical errors."
    ),

    "7": (
        "*7. How to Use*\n\n"
        "🗣 *Voice notes* — Speak naturally in English, Hindi, or Hinglish. "
        "The bot transcribes and processes your question. Best for quick "
        "questions on the go.\n\n"
        "⌨️ *Text* — Type your question in any of the three languages. Best "
        "for longer or more detailed questions.\n\n"
        "*Follow-ups* — After any answer you can ask things like _\"now show "
        "me for Gaya\"_ or _\"what about last year?\"_ — the bot keeps the "
        "conversation context for the last few exchanges. Use /clear to "
        "start fresh.\n\n"
        "📥 *Exports* — Every answer auto-generates a downloadable Excel "
        "file (full underlying data) and a PowerPoint slide (with native "
        "chart) alongside the in-chat reply."
    ),

    "8": (
        "*8. Tips for Better Answers*\n\n"
        "• *Be specific about geography* — say _\"Patna district\"_ or "
        "_\"Sadar block in Patna\"_ rather than just _\"Patna\"_.\n"
        "• *Use clear time periods* — _\"Q1 FY 2024-25\"_ or _\"April to "
        "June 2024\"_ is clearer than _\"last quarter\"_.\n"
        "• *Say \"coverage\" for %, \"count\" for absolute numbers* — "
        "_\"BCG coverage\"_ gives % achievement; _\"BCG count\"_ gives raw "
        "doses given.\n"
        "• *Add \"average\" for monthly average over a range* — see Section 3.\n"
        "• *Be explicit about ranking* — _\"top 10\"_, _\"lowest 5\"_, "
        "_\"show trend\"_.\n"
        "• *Voice notes work great in Hindi* — speak naturally, no need to "
        "translate."
    ),
}

# Map of single-word / short-phrase keywords → section id, used for fuzzy
# matching when the user types a topic name instead of a number.
_HELP_KEYWORDS: dict[str, str] = {
    # Section 1
    "queries": "1", "ask": "1", "examples": "1", "what can i ask": "1",
    # Section 2
    "quality": "2", "data quality": "2", "checks": "2",
    # Section 3
    "coverage": "3", "calculation": "3", "formula": "3",
    "how coverage is calculated": "3", "how is coverage calculated": "3",
    # Section 4
    "completeness": "4", "complete": "4", "data completeness": "4",
    # Section 5
    "correctness": "5", "correct": "5", "data correctness": "5",
    # Section 6
    "pre": "6", "pres": "6", "errors": "6",
    "reporting errors": "6", "probable reporting errors": "6",
    # Section 7
    "use": "7", "how to use": "7", "usage": "7",
    # Section 8
    "tips": "8", "better answers": "8", "best practices": "8",
}


def _match_help_section(text: str) -> str | None:
    """Return the section id (1-8) if `text` is a clear menu choice, else None.

    Accepts:
      • plain numbers '1' .. '8'
      • 'section 4', 'option 4', 'topic 4', etc.
      • exact keyword match ('completeness', 'pre', ...)
    """
    import re
    t = text.strip().lower().lstrip("•-*. ").rstrip(".?!")
    if not t:
        return None
    if t in _HELP_SECTIONS:
        return t
    m = re.match(r"^(?:section|option|topic|menu|number|item)\s*[#:]?\s*(\d+)$", t)
    if m and m.group(1) in _HELP_SECTIONS:
        return m.group(1)
    if t in _HELP_KEYWORDS:
        return _HELP_KEYWORDS[t]
    return None


def _is_command(text: str, *names: str) -> bool:
    """Match /name, name, or /Name."""
    t = text.strip().lower()
    return t in {f"/{n}".lower() for n in names} or t in {n.lower() for n in names}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _to_whatsapp_md(text: str) -> str:
    """
    Convert Markdown (which the LLM may produce) into WhatsApp-compatible
    formatting. WhatsApp uses single asterisks for bold, single underscores
    for italic — same as Telegram's "Markdown" v1 mode.

    - **bold**  → *bold*
    - # / ## / ### Heading  → *Heading*
    """
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'^#{1,3}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    return text


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message into chunks of at most max_len characters."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Inbound message processing
# ---------------------------------------------------------------------------

async def _process_message(
    user_id: str,
    user_name: str | None,
    message_text: str,
    message_id: str,
) -> None:
    """
    Run the full pipeline for an inbound text message and send the response
    back via the WhatsApp Cloud API.
    """
    # Show blue ticks immediately — before acquiring the per-user lock so the
    # user gets visual feedback even if a previous message is still processing.
    asyncio.create_task(whatsapp_client.mark_as_read(message_id))
    async with _get_user_lock(user_id):
        await _process_message_locked(user_id, user_name, message_text, message_id)


async def _process_message_locked(
    user_id: str,
    user_name: str | None,
    message_text: str,
    message_id: str,
) -> None:
    """Inner pipeline — always called with _get_user_lock(user_id) held."""
    audit_logger.start_capture()

    logger.info(f"Processing for user {user_id} ({user_name}): {message_text!r}")

    # Built-in "commands" — these don't run the pipeline
    if _is_command(message_text, "start"):
        greeting = f"Namaste *{user_name or 'there'}*! 🙏\n\n" + _HELP_MENU
        state = _get_state(user_id)
        state["state"] = "awaiting_help_choice"
        state["pending"] = {"menu": True}
        try:
            for chunk in _split_message(_to_whatsapp_md(greeting), 4096):
                await whatsapp_client.send_text(user_id, chunk)
        except Exception as exc:
            logger.warning(f"send /start text failed: {exc}")
        audit_logger.end_capture()
        return

    if _is_command(message_text, "help"):
        state = _get_state(user_id)
        state["state"] = "awaiting_help_choice"
        state["pending"] = {"menu": True}
        try:
            for chunk in _split_message(_to_whatsapp_md(_HELP_MENU), 4096):
                await whatsapp_client.send_text(user_id, chunk)
        except Exception as exc:
            logger.warning(f"send /help text failed: {exc}")
        audit_logger.end_capture()
        return

    if _is_command(message_text, "clear", "reset"):
        _user_data[user_id] = {"history": [], "state": "idle", "pending": {}}
        ack = ("Conversation history cleared. You can start a fresh question!"
               + _FOOTNOTE)
        try:
            for chunk in _split_message(_to_whatsapp_md(ack), 4096):
                await whatsapp_client.send_text(user_id, chunk)
        except Exception as exc:
            logger.warning(f"send /clear ack failed: {exc}")
        audit_logger.end_capture()
        return

    state = _get_state(user_id)
    start_ms = time.monotonic()

    trace: dict = {
        "geo_entity":         None,
        "generated_sql":      None,
        "sql_attempts":       1,
        "bq_row_count":       None,
        "chart_type":         None,
        "success":            False,
        "error_message":      None,
        "bq_rows":            [],
        "export_title":       "",
        "should_export_excel": True,
    }

    chart_buf = None
    try:
        if state["state"] == "awaiting_clarification":
            response, chart_buf = await _handle_clarification(user_id, message_text, trace)
        elif state["state"] == "awaiting_confirmation":
            response, chart_buf = await _handle_confirmation(user_id, message_text, trace)
        elif state["state"] == "awaiting_sql_clarification":
            response, chart_buf = await _handle_sql_clarification(user_id, message_text, trace)
        elif state["state"] == "awaiting_help_choice":
            response, chart_buf = await _handle_help_choice(user_id, message_text, trace)
        else:
            response, chart_buf = await _handle_new_question(user_id, message_text, trace)
    except Exception as exc:
        logger.error(f"Unexpected error for user {user_id}: {exc}", exc_info=True)
        trace["error_message"] = str(exc)
        response = _ERR

    elapsed_ms = int((time.monotonic() - start_ms) * 1000)
    backend_log = audit_logger.end_capture()

    _add_to_history(user_id, "user", message_text)
    _add_to_history(user_id, "assistant", response)

    await audit_logger.log_interaction(
        user_id=user_id,
        username=user_name,
        user_message=message_text,
        geo_entity=trace["geo_entity"],
        generated_sql=trace["generated_sql"],
        sql_attempts=trace["sql_attempts"],
        bq_row_count=trace["bq_row_count"],
        response_text=response,
        chart_type=trace["chart_type"],
        success=trace["success"],
        error_message=trace["error_message"],
        processing_time_ms=elapsed_ms,
        backend_log=backend_log,
    )

    # ---- Send the text reply ----
    # Append the global "Type /help to know more..." footnote to every
    # message except help-related ones (menu / sections / exit acks).
    response_md = _to_whatsapp_md(response)
    if not trace.get("is_help_response", False):
        response_md = response_md.rstrip() + _to_whatsapp_md(_FOOTNOTE)
    for chunk in _split_message(response_md, 4096):
        try:
            await whatsapp_client.send_text(user_id, chunk)
        except Exception as exc:
            logger.warning(f"send_text failed for user {user_id}: {exc}")

    # ---- Send the chart image ----
    if chart_buf:
        try:
            chart_buf.seek(0)
            chart_bytes = chart_buf.read()
            media_id = await whatsapp_client.upload_media(
                chart_bytes, "image/png", "chart.png"
            )
            await whatsapp_client.send_image(user_id, media_id)
        except Exception as exc:
            logger.warning(f"chart image send failed for user {user_id}: {exc}")

    # ---- Send Excel + PowerPoint exports ----
    bq_rows            = trace.get("bq_rows", [])
    export_title       = trace.get("export_title") or message_text[:60]
    should_export_excel = trace.get("should_export_excel", True)

    # Safety net — override the model's decision in cases where the data is
    # clearly worth a downloadable table:
    #   • 2+ rows (any list, ranking, trend, comparison, breakdown)
    #   • 1 row but with 3+ numeric columns (multi-metric breakdowns)
    # This catches cases where the chart-spec model returns false despite
    # the result being genuinely tabular.
    if bq_rows:
        if len(bq_rows) >= 2:
            should_export_excel = True
        elif len(bq_rows) == 1:
            numeric_cols = sum(
                1 for v in bq_rows[0].values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            )
            if numeric_cols >= 3:
                should_export_excel = True

    if bq_rows or chart_buf:
        try:
            upload_date = await asyncio.to_thread(bigquery_client.get_latest_upload_date)
        except Exception:
            upload_date = None

        if bq_rows and should_export_excel:
            try:
                excel_buf, fname = export_utils.generate_excel(export_title, bq_rows, upload_date)
                excel_buf.seek(0)
                media_id = await whatsapp_client.upload_media(
                    excel_buf.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    f"{fname}.xlsx",
                )
                await whatsapp_client.send_document(
                    user_id, media_id, f"{fname}.xlsx",
                    caption="📊 Excel export",
                )
            except Exception as exc:
                logger.warning(f"Excel export failed for user {user_id}: {exc}", exc_info=True)

        if chart_buf:
            try:
                chart_buf.seek(0)
                ppt_buf, fname = export_utils.generate_ppt(
                    export_title, chart_buf, upload_date,
                    chart_spec=trace.get("chart_spec"),
                    rows=bq_rows or None,
                )
                ppt_buf.seek(0)
                media_id = await whatsapp_client.upload_media(
                    ppt_buf.read(),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    f"{fname}.pptx",
                )
                await whatsapp_client.send_document(
                    user_id, media_id, f"{fname}.pptx",
                    caption="📎 PowerPoint slide",
                )
            except Exception as exc:
                logger.warning(f"PPT export failed for user {user_id}: {exc}", exc_info=True)


async def _process_voice(
    user_id: str,
    user_name: str | None,
    media_id: str,
    message_id: str,
) -> None:
    """
    Download a voice note from Meta, transcribe via Whisper, then route
    the transcribed text through the same _process_message pipeline.
    """
    asyncio.create_task(whatsapp_client.mark_as_read(message_id))

    try:
        await whatsapp_client.send_text(
            user_id, "🎙 Transcribing your voice message…"
        )
    except Exception:
        pass

    try:
        voice_bytes = await whatsapp_client.download_media(media_id)
    except Exception as exc:
        logger.error(f"voice download failed for user {user_id}: {exc}")
        try:
            await whatsapp_client.send_text(
                user_id,
                "Sorry, I couldn't download your voice message. Please try again."
                + _FOOTNOTE,
            )
        except Exception:
            pass
        return

    if len(voice_bytes) > 24 * 1024 * 1024:
        try:
            await whatsapp_client.send_text(
                user_id,
                "Voice message is too large (max ~24 MB). Please send a shorter clip."
                + _FOOTNOTE,
            )
        except Exception:
            pass
        return

    try:
        transcribed = await claude_client.transcribe_voice(voice_bytes)
    except Exception as exc:
        logger.error(f"transcription failed for user {user_id}: {exc}")
        transcribed = ""

    if not transcribed:
        try:
            await whatsapp_client.send_text(
                user_id,
                "Sorry, I couldn't transcribe that voice message. "
                "Please try again or type your question."
                + _FOOTNOTE,
            )
        except Exception:
            pass
        return

    try:
        await whatsapp_client.send_text(
            user_id, f"🎙 *I heard:* _{transcribed}_"
        )
    except Exception:
        pass

    await _process_message(user_id, user_name, transcribed, message_id)


# ---------------------------------------------------------------------------
# LRU eviction — prune stale users to prevent unbounded memory growth
# ---------------------------------------------------------------------------

async def _evict_stale_users() -> None:
    """Hourly background task: drop user state and locks not seen in 7 days."""
    _SEVEN_DAYS = 7 * 86_400
    while True:
        await asyncio.sleep(3_600)
        cutoff = time.time() - _SEVEN_DAYS
        stale = [uid for uid, data in _user_data.items()
                 if data.get("_last_seen", 0) < cutoff]
        for uid in stale:
            _user_data.pop(uid, None)
            _user_locks.pop(uid, None)
        if stale:
            logger.info(f"Evicted {len(stale)} stale user(s) from memory (inactive >7 days).")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    audit_logger.install_capture_handler()
    # Eagerly open the Sheets connection so misconfigurations show up in
    # the boot log instead of hiding inside the first message's background
    # write. Runs in a thread so a slow Google API call doesn't block boot.
    asyncio.create_task(asyncio.to_thread(audit_logger.warmup))

    async def _prewarm():
        try:
            cache = await schema_cache.ensure_loaded()
            logger.info(
                f"Schema cache ready: {len(cache.get('pairs', {}))} COALESCE pairs detected."
            )
        except Exception as exc:
            logger.warning(f"Schema cache pre-warm failed (will retry on first query): {exc}")

    asyncio.create_task(_prewarm())

    eviction_task = asyncio.create_task(_evict_stale_users())
    logger.info("HMIS WhatsApp Bot is ready to receive webhooks.")

    yield

    eviction_task.cancel()
    await whatsapp_client.close()
    logger.info("HMIS WhatsApp Bot shutting down.")


app = FastAPI(lifespan=lifespan, title="HMIS WhatsApp Bot")


@app.get("/")
async def root() -> dict:
    """Health-check endpoint — handy for uptime monitors."""
    return {"status": "ok", "service": "HMIS WhatsApp Bot"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    One-time GET handshake when configuring the webhook in Meta's dashboard.
    Meta sends ?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=...
    We echo back the challenge string if our verify token matches.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verification successful.")
        return Response(content=challenge or "", media_type="text/plain")
    logger.warning(
        f"Webhook verification failed: mode={mode!r}, token-match={token == WHATSAPP_VERIFY_TOKEN}"
    )
    raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/webhook")
async def receive_webhook(request: Request) -> Response:
    """
    Handle inbound WhatsApp events. We respond 200 immediately and process
    each message in a background task so Meta doesn't time out and retry.
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not whatsapp_client.verify_signature(body_bytes, signature):
        logger.warning("Webhook signature verification failed — rejecting.")
        raise HTTPException(status_code=403, detail="Bad signature")

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning(f"Could not parse webhook JSON: {exc}")
        return Response(status_code=200)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            messages = value.get("messages") or []
            contacts = value.get("contacts") or []

            for message in messages:
                msg_id = message.get("id", "")

                # Dedupe — Meta sometimes retries
                if msg_id and msg_id in _processed_message_ids:
                    continue
                if msg_id:
                    _processed_message_ids.add(msg_id)
                    if len(_processed_message_ids) > _PROCESSED_MAX:
                        keep = list(_processed_message_ids)[_PROCESSED_MAX // 2:]
                        _processed_message_ids.clear()
                        _processed_message_ids.update(keep)

                # Age filter — drop old queued messages on restart
                try:
                    ts = int(message.get("timestamp", "0"))
                    age_s = time.time() - ts
                    if age_s > _MAX_MESSAGE_AGE_SECONDS:
                        logger.info(f"Dropped stale message ({age_s:.0f}s old) from {message.get('from')}")
                        continue
                except (ValueError, TypeError):
                    pass

                user_id = message.get("from", "")
                if not user_id:
                    continue

                user_name = None
                for contact in contacts:
                    if contact.get("wa_id") == user_id:
                        user_name = contact.get("profile", {}).get("name")
                        break

                msg_type = message.get("type")
                if msg_type == "text":
                    text = (message.get("text", {}).get("body") or "").strip()
                    if text:
                        asyncio.create_task(
                            _process_message(user_id, user_name, text, msg_id)
                        )
                elif msg_type == "audio":
                    audio_id = message.get("audio", {}).get("id")
                    if audio_id:
                        asyncio.create_task(
                            _process_voice(user_id, user_name, audio_id, msg_id)
                        )
                else:
                    logger.info(f"Ignoring unsupported message type {msg_type!r} from {user_id}")
                    asyncio.create_task(
                        whatsapp_client.send_text(
                            user_id,
                            "I can only process text and voice messages right now. "
                            "Please send your question as text or a voice note.",
                        )
                    )

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting HMIS WhatsApp Bot...")
    uvicorn.run(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
