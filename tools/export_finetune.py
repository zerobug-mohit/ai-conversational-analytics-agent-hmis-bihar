"""
export_finetune.py
------------------
Offline script — reads audit_log.jsonl and exports fine-tuning data.

Usage
-----
# Export ALL successful interactions (SQL generation examples):
    python export_finetune.py --task sql

# Export ALL successful interactions (end-to-end response examples):
    python export_finetune.py --task response

# Export only rows marked "TRUE" in the "✓ Good Example" column of the sheet
# (requires a CSV export of the Interactions tab saved as audit_sheet.csv):
    python export_finetune.py --task sql --curated audit_sheet.csv

Output
------
Creates  finetune_<task>_<timestamp>.jsonl  in the current directory.
This file is ready to upload to OpenAI's fine-tuning API.

OpenAI fine-tuning docs:
    https://platform.openai.com/docs/guides/fine-tuning
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Make project root importable when this script is run from anywhere
# (e.g. `python tools/export_finetune.py` from project root, or directly).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── System prompt (copied from claude_client.py for fine-tuning context) ────
# When fine-tuning for SQL generation, we want the model to learn the mapping:
#   system prompt + user question + geo entity  →  correct SQL
# For response generation:
#   system prompt + user question + BQ results  →  final reply
#
# Import the live system prompt so the fine-tuning data always matches
# what the production model sees.
try:
    from claude_client import _SYSTEM_PROMPT as SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = "(system prompt not available)"


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def load_curated_ids(csv_path: Path) -> set[str]:
    """
    Read a CSV export of the Google Sheet and return interaction IDs where
    the '✓ Good Example' column is 'TRUE'.
    """
    import csv
    good_ids = set()
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("✓ Good Example", "").strip().upper() == "TRUE":
                good_ids.add(row.get("Interaction ID", "").strip())
    return good_ids


def to_sql_example(record: dict) -> dict | None:
    """Convert a record to an OpenAI fine-tuning example for SQL generation."""
    sql = (record.get("generated_sql") or "").strip()
    if not sql or sql.upper().startswith("CANNOT_GENERATE"):
        return None

    geo = record.get("geo_entity")
    user_content = (
        f'Generate the BigQuery SQL query to answer: "{record["user_message"]}"\n'
        f'Confirmed geo entity: {json.dumps(geo, default=str) if geo else "None — use conversation context."}'
    )

    return {
        "messages": [
            {"role": "system",    "content": SYSTEM_PROMPT},
            {"role": "user",      "content": user_content},
            {"role": "assistant", "content": sql},
        ]
    }


def to_response_example(record: dict) -> dict | None:
    """Convert a record to an OpenAI fine-tuning example for response generation."""
    response = (record.get("response_text") or "").strip()
    if not response:
        return None

    return {
        "messages": [
            {"role": "system",    "content": SYSTEM_PROMPT},
            {"role": "user",      "content": record["user_message"]},
            {"role": "assistant", "content": response},
        ]
    }


def main():
    parser = argparse.ArgumentParser(description="Export HMIS bot fine-tuning data")
    parser.add_argument(
        "--task",
        choices=["sql", "response"],
        required=True,
        help="sql = SQL generation examples | response = end-to-end response examples",
    )
    parser.add_argument(
        "--input",
        default=str(_PROJECT_ROOT / "data" / "audit_log.jsonl"),
        help="Path to audit_log.jsonl (default: data/audit_log.jsonl in project root)",
    )
    parser.add_argument(
        "--curated",
        default=None,
        help="Path to CSV export of the Google Sheet — only export rows marked ✓",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=1,
        help="Only include interactions that returned at least N BigQuery rows (default: 1)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found.", file=sys.stderr)
        sys.exit(1)

    records = load_jsonl(input_path)
    print(f"Loaded {len(records)} records from {input_path}")

    # Filter to successful interactions with data
    records = [r for r in records if r.get("success") and
               (r.get("bq_row_count") or 0) >= args.min_rows]
    print(f"  → {len(records)} successful with ≥{args.min_rows} BQ row(s)")

    # Filter to curated selection if requested
    if args.curated:
        curated_ids = load_curated_ids(Path(args.curated))
        records = [r for r in records if r.get("id") in curated_ids]
        print(f"  → {len(records)} after curated filter")

    # Convert to fine-tuning format
    converter = to_sql_example if args.task == "sql" else to_response_example
    examples = [ex for r in records if (ex := converter(r)) is not None]
    print(f"  → {len(examples)} exportable examples")

    if not examples:
        print("No examples to export. Exiting.")
        sys.exit(0)

    # Write output
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"finetune_{args.task}_{ts}.jsonl")
    with out_path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nExported {len(examples)} examples → {out_path}")
    print("\nNext steps:")
    print("  1. Review the file to verify quality.")
    print("  2. OpenAI recommends at least 50 examples for fine-tuning.")
    print(f"  3. Upload:  openai api fine_tuning.jobs.create -t {out_path} -m gpt-5.4-mini")


if __name__ == "__main__":
    main()
