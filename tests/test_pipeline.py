"""
test_pipeline.py  —  Run the full bot pipeline without Telegram.
Usage:  python tests/test_pipeline.py     (from project root)
"""
import asyncio
import io
import sys
import logging
from pathlib import Path

# Make project root importable when this script is run from anywhere
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Force UTF-8 output on Windows so special characters don't crash
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)

import bigquery_client
import claude_client

QUERIES = [
    "What is the penta1 coverage for Darbhanga district in December 2025?",
    "What is BCG coverage for Patna district in April 2024?",
    "What is FIC coverage for Gaya district for full FY 2024-25?",
    "What timeframe data is available?",
]

async def run_query(question: str):
    sep = "=" * 70
    print(f"\n{sep}\nQUERY: {question}\n{sep}")
    history = []

    # Step 1: geo extraction
    print("\n[1] Extracting geo entity...")
    geo = await claude_client.extract_geo_entity(question, history)
    print(f"    {geo}")

    confirmed_entity = None
    if geo.get("has_geo"):
        print(f"\n[2] Running geo lookup...")
        try:
            matches = bigquery_client.run_query(geo["lookup_sql"])
        except Exception as e:
            print(f"    ERROR: {e}")
            return
        print(f"    {len(matches)} match(es): {matches}")
        if not matches:
            print("    No geo matches. Stopping.")
            return
        confirmed_entity = matches[0]
        if len(matches) > 1:
            print(f"    Multiple matches — using first: {confirmed_entity}")
    else:
        print("\n[2] No geo entity — using conversation history.")

    # Step 3: SQL generation
    print(f"\n[3] Generating SQL...")
    sql = await claude_client.generate_sql(question, confirmed_entity, history)
    print(f"    SQL:\n{sql}")
    if sql.upper().startswith("CANNOT_GENERATE:"):
        print(f"    BLOCKED: {sql}")
        return

    # Step 4: Run SQL with retry
    print(f"\n[4] Running SQL on BigQuery...")
    results = None
    for attempt in range(3):
        try:
            results = bigquery_client.run_query(sql)
            print(f"    OK — {len(results)} row(s): {results}")
            break
        except Exception as e:
            err = str(e)
            print(f"    Attempt {attempt+1} failed: {err[:200]}")
            if "Unrecognized name" in err and attempt < 2:
                sql = await claude_client.fix_sql(sql, err, history)
                print(f"    Fixed SQL:\n{sql}")
                if sql.upper().startswith("CANNOT_GENERATE:"):
                    print(f"    Could not fix: {sql}")
                    return
            else:
                print("    Giving up.")
                return

    if not results:
        print("    No rows returned.")
        return

    # Step 5: Summarize
    print(f"\n[5] Summarizing...")
    summary = await claude_client.summarize_results(question, results, history)
    print(f"\n    FINAL ANSWER:\n{summary}")

async def main():
    for q in QUERIES:
        await run_query(q)
    print(f"\n{'='*70}\nAll tests done.")

asyncio.run(main())
