# HMIS Telegram Analytics Bot — Project Log

**Project directory:** `C:\Users\mchaurasiya\hmis-telegram-agent\`
**Last updated:** 2026-05-05
**Status:** Fully operational

---

## 1. What This Bot Does

A Telegram bot for Bihar's immunization program officers. A user sends a plain-language question (text or voice) in English, Hindi, or Hinglish (e.g. "What is BCG coverage for Patna in April 2024?"), and the bot:

1. Transcribes voice messages via OpenAI Whisper (if voice input is used)
2. Identifies any geographic name (district / block / facility) in the message
3. Looks it up in BigQuery to confirm the exact name and numeric code
4. If multiple matches, asks the user to clarify
5. Generates a BigQuery SQL query using strict HMIS rules
6. Runs the query; auto-retries and self-corrects on any `400` error (up to 3 attempts)
7. Summarizes the result in three sections: Answer · Analysis · Suggested Next Queries
8. Generates a chart image (from ~80 chart types, model-selected) and sends it as a photo
9. Optionally exports the result as an Excel file or PowerPoint presentation with a native editable chart
10. Logs the full interaction to Google Sheets and a local JSONL file

---

## 2. File Inventory

| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot entry point, state machine, pipeline orchestration |
| `claude_client.py` | All OpenAI API calls: geo extraction, SQL generation, SQL fixing, summarization, chart spec, conversational replies, Whisper voice transcription |
| `bigquery_client.py` | BigQuery connection, safety gate (SELECT-only), query execution |
| `chart.py` | Matplotlib chart generation (~80 chart types with McKinsey palette; collision-safe labels) |
| `export_utils.py` | Excel (`.xlsx`) and PowerPoint (`.pptx`) export; PPT includes native editable chart objects via python-pptx |
| `audit_logger.py` | Logs every interaction to Google Sheets + `audit_log.jsonl` |
| `export_finetune.py` | Offline script — exports curated rows to OpenAI fine-tuning JSONL |
| `config.py` | Loads and validates all environment variables from `.env` |
| `test_pipeline.py` | Runs the full pipeline without Telegram for rapid testing |
| `.env` | Live credentials (never committed to git) |
| `.env.txt` | Template showing all required variables |
| `requirements.txt` | Python dependencies |
| `CLAUDE.md` | Full BigQuery schema, HMIS rules, and domain glossary for the AI |
| `audit_log.jsonl` | Auto-created on first run; one JSON record per interaction |
| `service_account.json` | Google Cloud service account credentials |

---

## 3. Environment Variables (`.env`)

```
TELEGRAM_BOT_TOKEN=...          # From @BotFather
OPENAI_API_KEY=...              # From platform.openai.com
AI_MODEL_SQL=gpt-4o             # For SQL generation (complex rule-following)
AI_MODEL_FAST=gpt-4o-mini       # For geo extraction, summarization, chart spec
GOOGLE_APPLICATION_CREDENTIALS=service_account.json
BIGQUERY_PROJECT_ID=biharhmisdatafordvctool
MAX_HISTORY_EXCHANGES=10
AUDIT_SHEET_ID=1PDObwKi1TijGG_ZE-l3IS3qd3co5gvoO8EJLTAhNGH8
```

---

## 4. Dependencies (`requirements.txt`)

```
python-telegram-bot==21.5
openai>=1.56.0
google-cloud-bigquery==3.25.0
python-dotenv==1.0.1
matplotlib>=3.8.0
numpy>=1.26.0
squarify>=0.4.3        # treemap charts
gspread>=6.0.0
python-dateutil>=2.8.0 # date arithmetic for completeness queries
openpyxl>=3.1.0        # Excel export
python-pptx>=0.6.21    # PowerPoint export with native editable charts
Pillow>=9.0.0          # image sizing for PPT fallback
```

Install: `pip install -r requirements.txt`

---

## 5. BigQuery Data Source

- **Project:** `biharhmisdatafordvctool`
- **Dataset:** `BH_HMIS_Data`
- **Table:** `BH_HMIS_Facility_Level_Data_with_all_Targets`
- **Full path:** `` `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` ``
- **Rows available:** April 2024 – March 2026 (24 months)
- **Row cap:** 500 rows per query

---

## 6. Architecture: The Query Pipeline

```
User message — text OR voice (Telegram)
        │
        ▼ (voice only)
[0] transcribe_voice()            OpenAI Whisper — ogg → text (English/Hindi/Hinglish)
        │
        ▼
[1] extract_geo_entity()          gpt-4o-mini
        │ has_geo=true?
        ▼
[2] run_query(lookup_sql)         BigQuery — resolves name → numeric code
        │ 0 matches → error  |  2+ matches → ask user to clarify
        ▼
[3] generate_sql()                gpt-4o — applies all HMIS rules
        │ CANNOT_GENERATE? → answer_conversational()
        ▼
[4] run_query(sql)                BigQuery — up to 3 attempts with fix_sql() on 400 errors
        │
        ▼
[5] summarize_results()  ┐        gpt-4o-mini  (run in parallel)
    get_chart_spec()     ┘        gpt-4o-mini  — selects from ~80 chart types
        │
        ▼
[6] generate_chart()              matplotlib → PNG BytesIO (or None)
        │
        ▼
[7] reply_text() + reply_photo()  Telegram  (_to_telegram_md() fixes Markdown formatting)
        │
        ▼ (if user requested export)
[8] generate_excel() / generate_ppt()   export_utils.py
        │                                PPT → native editable chart (python-pptx) or image fallback
        ▼
[9] log_interaction()             Google Sheets + audit_log.jsonl  (background thread)
```

---

## 7. Key HMIS Rules Encoded in the System Prompt

### Coverage formula (critical)
```sql
ROUND(SUM(numerator_col) / (MAX(target_col) * COUNT(DISTINCT Month)) * 100, 1)
```
Target columns store the **monthly** target (annual ÷ 12), repeated in every facility row.
`COUNT(DISTINCT Month)` scales the denominator correctly for multi-month queries.
**Never** use `SUM(target_col)` — it multiplies by number of facility rows.

### Target column selection
| Scope | Column |
|-------|--------|
| Facility | `facility_targets_estimated_infants_202425` |
| Block | `block_targets_estimated_infants_202425` |
| District | `district_targets_estimated_infants_202425` |
| State | `state_targets_estimated_infants_202425` |
| ANC | `block_targets_estimated_pregnancy_202425` |

### COALESCE pairs
The table merges two HMIS format versions. Many indicators have two columns (old and new format); a given row has data in only one. All queries use `COALESCE(new_col, old_col)`.
Key pairs: Pentavalent 1/2/3, FIC male/female, OPV 0/1/2/3, HepB, IPV 1/2/3, Rotavirus 1/2/3, PCV 1/2/booster, MR/MCV1/MCV2, ANC registered, TD1/TD2/booster, and more (full list in `CLAUDE.md`).

### Other rules
- Always filter `infacility_or_outreach_or_total = 'Total'` unless user specifies otherwise
- `Month` is a STRING (`YYYY-MM-DD`) — never use `TIMESTAMP_SUB`; use `DATE_SUB(CURRENT_DATE(), INTERVAL N MONTH)` or compute literal strings from today's date (injected into every prompt)
- Always use numeric codes in WHERE clauses (`district_code = 212`, not `district_name = 'Patna'`)
- **Block-level filtering uses `sub_district_ulb_code` / `sub_district_ulb_name`**, NOT `block_code` / `block_name` — the block columns have data anomalies in the HMIS export. Block target columns (`block_targets_estimated_infants_202425`, `block_targets_estimated_pregnancy_202425`) are still correct for denominators.
- Only SELECT/WITH queries allowed — DDL/DML blocked at `bigquery_client.py`

---

## 8. Response Format

Every data answer has three sections:

**Section 1 — Answer**
Direct result with numerator, denominator, and coverage %. Indian number formatting (1,00,000).

**Section 2 — Analysis**
Public health interpretation. Flags low (<50%), moderate (50–80%), or high (>80%) coverage against the UIP 90% target. Notes patterns or gaps.

**Section 3 — Suggested Next Queries**
2–3 context-aware follow-up questions based on the current result and recent conversation history.

---

## 9. Chart Generation

`chart.py` generates charts using matplotlib with a non-interactive Agg backend. The AI (`get_chart_spec`) decides the chart type and returns a full spec. The model can select from ~80 chart types; `chart.py` implements the most-used set natively and falls back gracefully for unsupported types.

**Colour palette (McKinsey-style):** `#003D6B` (navy), `#2E86C1` (steel), `#148A72` (teal), `#D4860B` (amber), `#7B3FA0` (purple), `#1565A8` (blue)

| Chart type | When used |
|------------|-----------|
| `gauge` | Single coverage % value |
| `line` / `slope` | Trend over time; multi-series via `series_column` |
| `bar` / `horizontal_bar` | Category comparison (≤8 vs >8 items) |
| `grouped_bar` / `stacked_bar` / `stacked_column` | Multiple indicators side-by-side |
| `area` / `stacked_area` | Volume or composition over time |
| `pie` / `donut` | Part-to-whole proportions |
| `lollipop` | Ranked list (cleaner than bar for long lists) |
| `funnel` | Stage-by-stage drop-off |
| `waterfall` | Incremental contribution |
| `heatmap` | Block × month or district × indicator matrix |
| `treemap` | Proportional area by category |
| `scatter` / `bubble` / `connected_scatter` | Correlation / two-variable plots |
| `radar` | Multi-indicator profile |
| `dumbbell` | Before vs. after comparison |
| `none` | No visual needed |

**Label collision prevention:** `_annotate_line_points()` groups annotations by x-position, places the highest-value label above its marker, and flips any label within 8% of the y-axis range of an already-placed label to below the marker. A 14% top-headroom pad ensures the uppermost label never clips the axis edge. Legend uses `loc="best"` (auto-placed in the lowest-density corner).

**Formatting consistency with PPT:** Bars use solid fills (no `alpha`); area fills use `alpha=0.80` (was 0.20). This matches the PPT native chart output exactly.

Coverage charts include a dashed UIP Target line at 90%.
Charts are sent as Telegram photo messages immediately after the text response.

---

## 10. Export: Excel and PowerPoint

`export_utils.py` provides two export functions, both triggered by user intent detected in the pipeline.

### `generate_excel(title, rows, upload_date) → (BytesIO, filename)`
- Writes query rows to a styled `.xlsx` file using `openpyxl`
- Header row: navy fill (`#003D6B`), white bold Calibri 11pt
- Data rows: alternating white / light-blue (`#EBF5FB`) fill, Calibri 10pt
- All columns auto-sized; number format `#,##0.0` applied to float columns
- Sent as a Telegram document

### `generate_ppt(title, chart_buf, upload_date, *, chart_spec, rows) → (BytesIO, filename)`
- Slide layout: 13.33 × 7.5 in widescreen (1920 × 1080 pt equivalent)
- Title in top-left navy text box, upload date in top-right grey
- **Native editable chart** (python-pptx `add_chart`) for all supported types:
  `bar`, `horizontal_bar`, `grouped_bar`, `stacked_bar`, `stacked_column`, `stacked_horizontal_bar`, `line`, `slope`, `area`, `stacked_area`, `pie`, `donut`, `radar`, `scatter`, `connected_scatter`, `bubble`
- Falls back to PNG image (`add_picture`) for unsupported types (funnel, heatmap, waterfall, treemap, dumbbell, lollipop, etc.)
- **`_style_chart()` formatting** matches `chart.py` output exactly:
  - McKinsey palette (`_PALETTE_RGB`)
  - Trebuchet MS 10pt axis labels, #555555
  - White chart/plot area; #CCCCCC 0.9pt border
  - Legend at BOTTOM, Trebuchet MS 10pt, no frame, hidden for single-series charts
  - Line series: 2.5pt line, CIRCLE markers (white fill, coloured border 2.2pt, size 7)
  - Bar data labels: INSIDE_END white bold 10pt; stacked → CENTER white 9pt; line → ABOVE dark 9pt
  - Value axis: #F0F0F0 gridlines 0.8pt; category axis: no gridlines; both #DDDDDD spine

---

## 11. Voice Input

Users can send Telegram voice messages (OGG/Opus format). The `voice_handler` in `bot.py`:

1. Rejects files >24 MB
2. Downloads the audio bytes via Telegram Bot API
3. Calls `claude_client.transcribe_voice(audio_bytes)` which sends to **OpenAI Whisper** (`whisper-1`)
4. Whisper prompt includes HMIS context ("immunization", "BCG", "Bihar", "Hinglish") to improve domain accuracy
5. Echoes the transcription back to the user as `🎙 I heard: _<text>_`
6. Passes the transcribed text into the same `_process_message()` pipeline as typed messages

`message_handler` was refactored into `_process_message(update, message_text)` so both text and voice share a single pipeline path.

---

## 12. Telegram Markdown Formatting

Telegram's Markdown v1 (used by `parse_mode="Markdown"`) uses `*bold*` (single asterisk), not `**bold**`. The AI model (trained on CommonMark) outputs double asterisks.

`_to_telegram_md(text)` in `bot.py` converts:
- `**bold**` → `*bold*`
- `# Heading` / `## Heading` / `### Heading` → `*Heading*`

This is applied to every text message before it is sent to Telegram.

---

## 13. Audit Logging

### Google Sheet (`AUDIT_SHEET_ID=1PDObwKi1TijGG_ZE-l3IS3qd3co5gvoO8EJLTAhNGH8`)

Tab: **Interactions**

Columns: Timestamp · Interaction ID · User ID · Username · User Message · Geo Entity · Generated SQL · SQL Attempts · BQ Rows Returned · Response Text · Chart Type · Success · Error Message · Processing Time (ms) · **✓ Good Example** · Notes

The last two columns are filled manually for quality control.

### `audit_log.jsonl`

One JSON record per line. Fields match the sheet columns, plus a `finetuning` block with stubs for SQL-generation and response-generation training examples.

### Setup notes
- The service account (`bh-hmis-data@biharhmisdatafordvctool.iam.gserviceaccount.com`) must have **Editor** access on the sheet
- Google Sheets API and Google Drive API must both be enabled in the Cloud project
- If Sheets write fails, the JSONL write still succeeds — interactions are never lost

---

## 14. Fine-tuning Workflow

When enough interactions have been collected:

```bash
# Export all successful SQL generation examples
python export_finetune.py --task sql

# Export only rows ticked ✓ in the Google Sheet
# (Download the Interactions tab as CSV from Google Sheets first)
python export_finetune.py --task sql --curated audit_sheet.csv

# Export end-to-end response examples
python export_finetune.py --task response --curated audit_sheet.csv

# Upload to OpenAI for fine-tuning
openai api fine_tuning.jobs.create -t finetune_sql_YYYYMMDD.jsonl -m gpt-4o-mini
```

OpenAI recommends at least 50 high-quality examples before fine-tuning.

---

## 15. Bugs Fixed During Development

| Error | Root cause | Fix |
|-------|-----------|-----|
| `AsyncClient.__init__() got unexpected keyword argument 'proxies'` | `openai==1.54.0` incompatible with newer httpx | Upgraded to `openai>=1.56.0` |
| `CANNOT_GENERATE: time period beyond data availability` | GPT-4o conflating training cutoff with BigQuery data availability | Added Rule 8: never refuse based on time period |
| `Unrecognized name: _9_1_6_child_immunisation_pentavalent_1` | Intermittent column ambiguity | Added `fix_sql()` + retry loop |
| Coverage 0.1% (SUM instead of MAX on target) | Target column repeated in every facility row; SUM multiplied by row count | Changed formula to `MAX(target_col)` |
| FIC coverage 1088.9% (multi-month not scaled) | Summing 12 months of numerators against 1 monthly target | Added `COUNT(DISTINCT Month)` to denominator |
| Summaries showing fabricated numbers | Model omitting raw numerator/denominator from SELECT | Instructed model to always include all 3 columns |
| Hindi reply to English question | Language detection rule too loose | Tightened Rule 7: reply in same language as question |
| `TIMESTAMP_SUB does not support MONTH` | Model used TIMESTAMP_SUB for relative date arithmetic | Added Rule 10; inject today's date into every SQL prompt |
| Conversational follow-ups returning error | `CANNOT_GENERATE` treated as error instead of routing to context-aware reply | Added `answer_conversational()` fallback |
| Service account Drive quota exceeded | Bot tried to create sheet in service account's own Drive | User creates sheet manually, shares with service account |
| Block queries returning wrong/no results | `block_code`/`block_name` columns have data anomalies in the HMIS export | Switched all block-level filtering to `sub_district_ulb_code`/`sub_district_ulb_name`; block target columns unchanged |
| `**bold**` showing as literal asterisks in Telegram | Telegram Markdown v1 uses `*bold*`; model outputs CommonMark `**bold**` | Added `_to_telegram_md()` converter in `bot.py` |
| Bar charts appeared washed-out vs PPT | `alpha=0.93` on bars and `alpha=0.20` on area fills | Removed bar alpha; area fill raised to `alpha=0.80` |
| Data labels overlapping on multi-series line charts | All labels placed +8pt above marker regardless of proximity | Replaced inline annotations with `_annotate_line_points()` — flips close labels below marker; added 14% top headroom |
| Legend overlapping data on line/area charts | `loc="lower center"` placed legend inside plot area | Changed to `loc="best"` across `_line()` and `_area()` |
| PRE B3 check under-counting — 28.2% vs 28.4% (Darbhanga Feb-26) | B3 guard was `v_sess_held IS NOT NULL AND v_sess_held > 0 AND v_aefi_minor = 0`, excluding facilities with 0 sessions | Removed `v_sess_held > 0` requirement; B3 fires whenever `v_aefi_minor = 0` |
| PRE B3 under-counting — 16/19 vs 19/19 (Tardih Mar-26) | Same root cause: 3 facilities had `sess_held=0` + `aefi=0`, excluded by sessions > 0 guard | Same fix as above |
| PRE B3 under-counting — 432 vs 433 facilities (Darbhanga Mar-26) | Sub Divisional Hospital Biraul (code 432087) had `v_sess_held=NULL`, `v_aefi_minor=0`. Guard `v_sess_held IS NOT NULL` excluded it | Changed B3 guard from `v_sess_held` to `v_aefi_minor`: `IF(v_aefi_minor = 0, 1, 0)` |
| PRE B3 regression to 299 facilities after removing all IS NOT NULL guards | LLM ignores template and generates its own B3 SQL with `v_sess_held IS NOT NULL AND v_sess_held > 0` — regressing to old wrong logic | Added explicit CRITICAL warning in CLAUDE.md B3 row (with exact SQL and four "Do NOT" rules banning v_sess_held). Added CRITICAL comment in claude_client.py template |

---

## 16. How to Run

```bash
# Start the bot
cd C:\Users\mchaurasiya\hmis-telegram-agent
python bot.py

# Test the pipeline without Telegram
python test_pipeline.py

# Export fine-tuning data
python export_finetune.py --task sql
python export_finetune.py --task response
```

---

## 17. Known Limitations / Future Improvements

- **In-memory history:** Conversation history resets when `bot.py` restarts. To persist across restarts, serialize `_user_data` to a file or SQLite database.
- **Single table:** All queries target one table. If new FY targets are added (e.g. `_202526` columns), the system prompt and COALESCE pairs need updating.
- **No user authentication:** Any Telegram user who finds the bot can query it. Add a whitelist of allowed user IDs to `config.py` if needed.
- **Chart for single values:** A gauge is shown even for a single non-coverage metric (e.g. total sessions). Could add a threshold: only show gauge for percentages.
- **Fine-tuning not yet done:** Logging infrastructure is in place; fine-tuning can begin once 50+ quality interactions are collected and curated in the sheet.
- **Voice language detection:** Whisper transcribes well but Hinglish (mixed Hindi-English) may occasionally be transcribed in one language only. The domain-specific Whisper prompt mitigates this.
