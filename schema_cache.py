"""
schema_cache.py
---------------
Detects COALESCE pairs for the HMIS BigQuery table by:

  1. Querying INFORMATION_SCHEMA for the current column list.
  2. Checking a persistent JSON cache (schema_coalesce_cache.json) —
     valid when column count matches AND cache is < 7 days old.
  3. On a cache miss, sending the indicator column list to the LLM, which
     semantically groups columns measuring the same HMIS indicator across
     format versions (e.g. FIC 9-12m male ↔ FIC 9-11m male, AEFI minor
     ↔ AEFI abscess, ANC registered ↔ ANC new registered, etc.).
  4. Falling back to regex suffix-matching if the LLM call fails.
  5. Persisting the LLM result to disk so subsequent startups are instant.

WHY LLM-BASED DETECTION
-----------------------
HMIS publishes rolling format versions that rename indicators. Regex suffix-
matching catches ~80% of pairs but misses cases where the description also
changed between versions. The LLM understands HMIS naming conventions and
matches those cases semantically.

PUBLIC API
----------
  await schema_cache.ensure_loaded()   → dict  (async, idempotent)
  schema_cache.get_rule4_text()        → str   (sync, call after ensure_loaded)
  schema_cache.invalidate()                    (clears memory + cache file)
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE   = "BH_HMIS_Facility_Level_Data_with_all_Targets"
_DATASET = "biharhmisdatafordvctool.BH_HMIS_Data"
_CACHE_FILE        = Path("data") / "schema_coalesce_cache.json"
_CACHE_MAX_AGE_SEC = 7 * 24 * 3600   # 7 days

# ---------------------------------------------------------------------------
# Columns to exclude before sending to the LLM (geography + admin + targets)
# ---------------------------------------------------------------------------
_SKIP_EXACT = frozenset({
    "Month",
    "state_code", "state",
    "district_code", "district_name", "district_category",
    "sub_district_ulb_code", "sub_district_ulb_name",
    "block_code", "block_name",
    "health_block_code", "health_block_name",
    "local_assembly_constituency", "parliament_constituency",
    "facility_type", "facility_code", "facility_name",
    "facility_sub_type", "facility_nin_number",
    "ulb_code", "ulb_name",
    "category", "sanctioned_bed_count", "functional_bed_count",
    "rural_urban", "ownership", "physical_notional", "status",
    "active_from_date", "active_to_date", "upload_date",
    "infacility_or_outreach_or_total", "providing_outreach_service",
    "ownership_classification", "format_type", "administrative_body",
    "providing_ayush_services", "ayush_stream", "co_located_ayush_facility",
    "hfr_id",
})
_SKIP_PREFIXES = (
    "facility_targets_",
    "block_targets_",
    "district_targets_",
    "state_targets_",
)

# Regex for fallback — strips leading numeric/module prefix from column name
_PREFIX_RE = re.compile(r'^(?:[a-z]\d+_)?(?:_\d+[a-z]?)+_')

# ---------------------------------------------------------------------------
# In-memory cache + async lock (lazy-initialised to avoid event-loop issues)
# ---------------------------------------------------------------------------
_cache: dict | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ensure_loaded() -> dict:
    """
    Fetch INFORMATION_SCHEMA, detect COALESCE pairs, cache result.
    Idempotent — safe to await on every SQL generation call.
    """
    global _cache
    if _cache is not None:
        return _cache
    async with _get_lock():
        if _cache is not None:   # double-check after acquiring lock
            return _cache
        _cache = await _do_load()
    return _cache


def get_rule4_text() -> str:
    """Return the COALESCE injection string. Call after ensure_loaded() has resolved."""
    return (_cache or {}).get("rule4_text", "")


def invalidate() -> None:
    """Force a fresh detection on the next ensure_loaded() call."""
    global _cache
    _cache = None
    _CACHE_FILE.unlink(missing_ok=True)
    logger.info("schema_cache: invalidated")


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------

async def _do_load() -> dict:
    rows = await asyncio.to_thread(_fetch_columns_sync)

    # Fix 2 — BQ unavailable at startup: serve stale disk cache rather than
    # leaving the bot with zero COALESCE pairs for its entire lifetime.
    if not rows:
        logger.error("schema_cache: INFORMATION_SCHEMA returned no rows")
        stale = _read_stale_cache()
        if stale:
            return stale
        logger.error("schema_cache: no disk cache either — COALESCE pairs unavailable")
        return {"columns": [], "pairs": {}, "rule4_text": ""}

    all_columns    = [r["column_name"] for r in rows]
    column_count   = len(all_columns)
    indicator_rows = [r for r in rows if _is_indicator(r["column_name"])]

    # Try reading from the on-disk JSON cache first
    pairs = _read_json_cache(column_count)

    if pairs is None:
        logger.info(
            f"schema_cache: cache miss — calling LLM to detect pairs "
            f"({column_count} total, {len(indicator_rows)} indicator columns)"
        )
        try:
            pairs = await _llm_detect_pairs(indicator_rows)
            _write_json_cache(column_count, pairs)
            logger.info(f"schema_cache: LLM detected {len(pairs)} COALESCE pairs — written to disk")
        except Exception as exc:
            logger.warning(f"schema_cache: LLM detection failed — regex fallback: {exc}")
            pairs = _regex_detect_pairs(indicator_rows)
    else:
        logger.info(f"schema_cache: loaded {len(pairs)} COALESCE pairs from disk cache")

    rule4_text = _build_rule4_text(pairs)
    logger.info(f"schema_cache: ready — {column_count} columns, {len(pairs)} pairs")
    return {"columns": all_columns, "pairs": pairs, "rule4_text": rule4_text}


# ---------------------------------------------------------------------------
# BigQuery helper (synchronous — runs in a thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _fetch_columns_sync() -> list[dict]:
    query = (
        f"SELECT column_name, ordinal_position "
        f"FROM `{_DATASET}`.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_name = '{_TABLE}' "
        f"ORDER BY ordinal_position"
    )
    try:
        import bigquery_client
        return bigquery_client.run_query(query)
    except Exception as exc:
        logger.error(f"schema_cache: INFORMATION_SCHEMA query failed: {exc}")
        return []


def _is_indicator(col: str) -> bool:
    """True for health-indicator columns; False for geography/admin/target columns."""
    if col in _SKIP_EXACT:
        return False
    return not any(col.startswith(p) for p in _SKIP_PREFIXES)


# ---------------------------------------------------------------------------
# Persistent JSON cache helpers
# ---------------------------------------------------------------------------

def _read_json_cache(column_count: int) -> dict | None:
    """Return pairs dict if cache is valid; None if stale, wrong count, or missing."""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("column_count") != column_count:
            logger.info("schema_cache: column count changed — cache invalidated")
            return None
        age = time.time() - data.get("generated_at", 0)
        if age > _CACHE_MAX_AGE_SEC:
            logger.info(f"schema_cache: cache is {age/3600:.1f}h old — invalidated")
            return None
        return _pairs_list_to_dict(data.get("pairs", []))
    except Exception as exc:
        logger.warning(f"schema_cache: error reading cache file: {exc}")
        return None


def _read_stale_cache() -> dict | None:
    """
    Read the disk cache ignoring age and column count — last resort when BQ
    is unavailable.  Returns a populated result dict or None if cache is absent.
    """
    if not _CACHE_FILE.exists():
        return None
    try:
        data  = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        pairs = _pairs_list_to_dict(data.get("pairs", []))
        if not pairs:
            return None
        age_h = (time.time() - data.get("generated_at", 0)) / 3600
        logger.warning(
            f"schema_cache: BQ unavailable — serving stale cache "
            f"({age_h:.1f}h old, {len(pairs)} pairs)"
        )
        rule4_text = _build_rule4_text(pairs)
        return {"columns": [], "pairs": pairs, "rule4_text": rule4_text}
    except Exception as exc:
        logger.warning(f"schema_cache: could not read stale cache: {exc}")
        return None


def _write_json_cache(column_count: int, pairs: dict) -> None:
    """Persist COALESCE pairs to disk."""
    try:
        pairs_list = [cols for cols in pairs.values() if len(cols) >= 2]
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(
                {"generated_at": time.time(), "column_count": column_count, "pairs": pairs_list},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"schema_cache: could not write cache file: {exc}")


def _pairs_list_to_dict(pairs_list: list) -> dict:
    """Convert [[col_a, col_b], ...] to {descriptor: [col_a, col_b], ...}."""
    result = {}
    for group in pairs_list:
        if isinstance(group, list) and len(group) >= 2:
            desc = _descriptor(group[0])
            if desc in result:
                desc = group[0]   # collision: use full column name
            result[desc] = group
    return result


# ---------------------------------------------------------------------------
# Fix 1 — hallucination guard: drop any column name the LLM invented
# ---------------------------------------------------------------------------

def _validate_llm_groups(groups: list, known_cols: set) -> list:
    """
    For each group returned by the LLM, remove any column name that does not
    exist in the INFORMATION_SCHEMA result (hallucinated names).  Drop groups
    that shrink below 2 real columns after filtering.

    Logs a warning for every hallucinated name so issues are visible in the
    bot logs without crashing anything.
    """
    validated = []
    for group in groups:
        if not isinstance(group, list):
            continue
        real       = [c for c in group if c in known_cols]
        invented   = [c for c in group if c not in known_cols]
        if invented:
            logger.warning(
                f"schema_cache: LLM hallucinated column(s) — dropped: {invented}"
            )
        if len(real) >= 2:
            validated.append(real)
    return validated


# ---------------------------------------------------------------------------
# LLM-based pair detection
# ---------------------------------------------------------------------------

async def _llm_detect_pairs(indicator_rows: list[dict]) -> dict:
    """
    Send the indicator column list to the LLM; ask it to group columns that
    measure the same HMIS indicator across format versions.
    Returns {descriptor: [newer_col, older_col, ...], ...}.
    """
    from openai import AsyncOpenAI
    from config import OPENAI_API_KEY, AI_MODEL_SQL

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    col_lines = "\n".join(
        f"{r['ordinal_position']}: {r['column_name']}"
        for r in indicator_rows
    )

    prompt = f"""You are an HMIS (Health Management Information System) data analyst for India.

Below is the complete list of health-indicator column names from a Bihar HMIS BigQuery table, listed in ordinal-position order (columns with higher position numbers were added more recently, i.e. they belong to the newer HMIS format version).

CONTEXT: The table merges data from two HMIS reporting format versions. Many indicators appear TWICE under different column names — once in the older format, once in the newer. A given facility-month row has data in only one of the two columns (the other is NULL). We need COALESCE expressions to handle both versions.

YOUR TASK: Identify all groups of 2+ columns that measure the SAME health indicator across different format versions.

RULES:
1. Only group columns that truly measure the same underlying indicator. Do NOT group different indicators together even if names look similar.
2. Within each group, put the NEWER column FIRST (higher ordinal position = newer).
3. Only include groups with 2 or more columns. Skip single-column indicators.
4. Use your knowledge of HMIS naming conventions to match columns whose descriptions changed between versions — for example:
   - "fully_immunized_children_aged_between_9_and_12_months_male" and "children_aged_between_9_and_11_months_fully_immunized_male" are the same (FIC male)
   - "total_number_of_new_pregnant_women_registered_for_anc" and "total_number_of_pregnant_women_registered_for_anc" are the same (ANC registered)
   - "aefi_minor_eg_fever_rash_pain_etc" and "aefi_abscess" are the same (AEFI minor category)
   - "home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm" and "...anm_midwife" are the same (SBA home delivery)

COLUMN LIST (format: ordinal_position: column_name):
{col_lines}

Return ONLY a JSON array of arrays. Each inner array is one group of equivalent columns, newest (highest ordinal) first:
[
  ["newer_column_name", "older_column_name"],
  ["newer_column_name", "older_column_name"],
  ...
]

Pure JSON only. No explanation, no markdown code fences."""

    response = await client.chat.completions.create(
        model=AI_MODEL_SQL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_completion_tokens=8000,
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown fences in case the model adds them despite instructions
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)

    groups = json.loads(raw)
    if not isinstance(groups, list):
        raise ValueError(f"LLM returned unexpected type: {type(groups)}")

    # Fix 1 — strip hallucinated column names before building the pairs dict
    known_cols = {r["column_name"] for r in indicator_rows}
    groups = _validate_llm_groups(groups, known_cols)
    logger.info(
        f"schema_cache: LLM returned {len(groups)} valid groups "
        f"(after hallucination check)"
    )

    return _pairs_list_to_dict(groups)


# ---------------------------------------------------------------------------
# Regex fallback pair detection
# ---------------------------------------------------------------------------

def _regex_detect_pairs(indicator_rows: list[dict]) -> dict:
    """
    Strip leading numeric prefix from each column name; columns with identical
    stripped suffixes are COALESCE candidates. Catches ~80% of pairs.
    """
    ordinals = {r["column_name"]: r["ordinal_position"] for r in indicator_rows}
    groups: dict[str, list[str]] = {}
    for r in indicator_rows:
        col  = r["column_name"]
        desc = _descriptor(col)
        groups.setdefault(desc, []).append(col)

    return {
        desc: sorted(cols, key=lambda c: ordinals[c], reverse=True)
        for desc, cols in groups.items()
        if len(cols) > 1
    }


def _descriptor(col: str) -> str:
    """Return the column name with its leading numeric/module prefix stripped."""
    m = _PREFIX_RE.match(col)
    return col[m.end():] if m else col


# ---------------------------------------------------------------------------
# Formatter — builds the COALESCE injection text for the SQL generation prompt
# ---------------------------------------------------------------------------

def _build_rule4_text(pairs: dict) -> str:
    if not pairs:
        return ""

    lines = [
        "=== LIVE COALESCE PAIRS (auto-detected from current BigQuery schema) ===",
        "The table stores two HMIS format versions for many indicators.",
        "For every indicator below, use the COALESCE expression shown — newer column first.",
        "These OVERRIDE any hardcoded column names from your training data.",
        "",
    ]
    for desc in sorted(pairs):
        cols = pairs[desc]
        coalesce = f"COALESCE({', '.join(cols)})"
        lines.append(f"• {desc}")
        lines.append(f"  → {coalesce}")

    lines += [
        "",
        "NOTE: Indicators not listed above are single-version — use them directly.",
        "If an expected COALESCE pair is missing, the schema may have been consolidated",
        "into a single column in the current format. Trust this list over training data.",
    ]
    return "\n".join(lines)
