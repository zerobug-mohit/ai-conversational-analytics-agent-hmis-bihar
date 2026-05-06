"""
bigquery_client.py
------------------
Handles all communication with Google BigQuery.

Key responsibilities:
  - Creates an authenticated BigQuery client using the service account JSON.
  - Runs SQL queries and returns the results as a list of dictionaries.
  - Enforces a hard safety rule: only SELECT/WITH queries are ever executed.
    Any query containing DROP, DELETE, UPDATE, INSERT, TRUNCATE, CREATE, ALTER
    or MERGE is rejected before reaching BigQuery.
  - Logs every query and its result count to the terminal.
"""

import logging
import os
import re
import time as _time
from google.cloud import bigquery
from config import GOOGLE_APPLICATION_CREDENTIALS, BIGQUERY_PROJECT_ID

logger = logging.getLogger(__name__)

# Keywords that must never appear in an executed query (whole-word match)
_FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT",
    "TRUNCATE", "CREATE", "ALTER", "MERGE",
]

# Maximum number of rows to return from any single query
_MAX_ROWS = 500


def _get_client() -> bigquery.Client:
    """Create and return an authenticated BigQuery client."""
    # Point the Google SDK to the service account file
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS
    return bigquery.Client(project=BIGQUERY_PROJECT_ID)


def is_safe_query(sql: str) -> tuple[bool, str]:
    """
    Check whether a SQL string is safe to execute.

    Returns:
        (True, "")           — safe
        (False, reason_str)  — unsafe, with explanation
    """
    sql_upper = sql.upper()

    # 1. Reject any forbidden DML / DDL keyword (whole-word)
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(r"\b" + keyword + r"\b", sql_upper):
            return False, f"Query contains forbidden keyword '{keyword}'. Only SELECT queries are allowed."

    # 2. Strip SQL comments, then verify the query starts with SELECT or WITH
    stripped = re.sub(r"--[^\n]*", "", sql)          # remove single-line comments
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)  # remove block comments
    stripped = stripped.strip()

    first_word = stripped.split()[0].upper() if stripped.split() else ""
    if first_word not in ("SELECT", "WITH"):
        return False, f"Query must begin with SELECT or WITH, but starts with '{first_word}'."

    return True, ""


def run_query(sql: str) -> list[dict]:
    """
    Execute a BigQuery SQL query and return results as a list of dicts.

    Args:
        sql: A SELECT or WITH query string.

    Returns:
        List of dicts, one per row. Column names are the dict keys.

    Raises:
        ValueError:    If the query fails the safety check.
        RuntimeError:  If BigQuery returns an error during execution.
    """
    # --- Safety gate ---
    safe, reason = is_safe_query(sql)
    if not safe:
        logger.warning(f"UNSAFE QUERY REJECTED: {reason}\nSQL:\n{sql}")
        raise ValueError(f"Unsafe query blocked: {reason}")

    logger.info(f"Running BigQuery query:\n{sql}\n")

    try:
        client = _get_client()
        query_job = client.query(sql)
        result = query_job.result()  # blocks until the query finishes

        rows = []
        for row in result:
            rows.append(dict(row))
            if len(rows) >= _MAX_ROWS:
                logger.warning(f"Query hit the {_MAX_ROWS}-row cap — results truncated.")
                break

        logger.info(f"Query returned {len(rows)} row(s).")
        return rows

    except ValueError:
        # Re-raise our own safety ValueError unchanged
        raise
    except Exception as exc:
        logger.error(f"BigQuery execution error: {exc}", exc_info=True)
        raise RuntimeError(f"BigQuery query failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Upload-date cache — used for export file footers
# ---------------------------------------------------------------------------
_upload_date_cache: dict = {"value": None, "fetched_at": 0.0}
_UPLOAD_DATE_TTL = 86_400  # 24 hours


def get_latest_upload_date() -> str | None:
    """
    Return MAX(upload_date) from the HMIS table, cached for 24 hours.
    Returns None gracefully on any error — callers should treat None as
    "upload date unavailable".
    """
    now = _time.time()
    if (
        _upload_date_cache["value"] is not None
        and (now - _upload_date_cache["fetched_at"]) < _UPLOAD_DATE_TTL
    ):
        return _upload_date_cache["value"]
    try:
        sql = (
            "SELECT MAX(upload_date) AS latest_upload_date "
            "FROM `biharhmisdatafordvctool.BH_HMIS_Data"
            ".BH_HMIS_Facility_Level_Data_with_all_Targets` "
            "WHERE infacility_or_outreach_or_total = 'Total'"
        )
        rows = run_query(sql)
        val = str(rows[0]["latest_upload_date"]) if rows and rows[0].get("latest_upload_date") else None
        _upload_date_cache["value"]      = val
        _upload_date_cache["fetched_at"] = now
        logger.info(f"Latest upload_date cached: {val}")
        return val
    except Exception as exc:
        logger.warning(f"Could not fetch latest upload_date: {exc}")
        return None
