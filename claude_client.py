"""
claude_client.py
----------------
All communication with the OpenAI API.

Public async functions:
  1. extract_geo_entity(message, history)
        → Identifies all geographic names the user mentioned and returns
          BigQuery LIKE-based lookup SQL to resolve each one to a code.

  2. generate_sql(question, confirmed_entities, history)
        → Generates the final analytics SQL query using the confirmed
          numeric codes and all HMIS rules (COALESCE pairs, coverage
          formula, correct target columns, standard filters).

  3. summarize_results(question, results, history)
        → Turns raw BigQuery rows into a plain-English (or Hindi)
          summary suitable for WhatsApp, following the HMIS response
          guidelines (≤300 words, Indian number formatting, etc.).

All calls share the same rich HMIS system prompt so the model always
has full context about the schema, rules, and domain vocabulary.

Models are configured in .env:
  AI_MODEL_SQL  — primary model for SQL generation, analysis, reasoning
                  (default: gpt-5.4)
  AI_MODEL_FAST — fast model for geo extraction, chart spec, simple tasks
                  (default: gpt-5.4-mini)
"""

import asyncio
import json
import logging
import re
from datetime import date

from openai import AsyncOpenAI
from config import OPENAI_API_KEY, AI_MODEL_SQL, AI_MODEL_FAST

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# SYSTEM PROMPT — contains the full HMIS context Claude needs
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are an expert HMIS (Health Management Information System) analytics assistant
for Bihar's immunization and maternal-health program. You help field officers query a
Google BigQuery table using plain-language questions.

=== REASONING APPROACH (read this before everything else) ===
Reason from the user's INTENT, not from pattern-matching to templates.

Steps for every request:
1. Understand what the user actually wants — even if phrased informally, in Hindi, or
   with date shorthands like "nov25", "Q3FY26", "aug25 mein", etc.
2. Identify the relevant schema elements (indicators, geography, time period).
3. Choose the right SQL approach — see the full query-type guide below.
4. Apply only the rules that are relevant to the query type (e.g. COALESCE pairs
   always apply; the coverage formula only applies when asking for coverage %).

The rules below are non-negotiable for correctness — but use judgment about how to
construct the SQL when the user's question doesn't fit a single neat template.
When in doubt, generate the SQL and let BigQuery return an empty result set rather
than refusing or asking a clarifying question.

=== SUPPORTED QUERY TYPES — the bot handles ALL of these ===

This bot is not limited to coverage and data-quality queries. Any analytical
question about the HMIS data is valid. Common query types include:

1. COVERAGE / PROPORTION-OF-TARGET  (apply Rule 2 + Rule 3)
   "BCG coverage in Patna", "what % of infants were fully immunised?"
   Use: SUM(numerator) / (MAX(target) * COUNT(DISTINCT Month)) * 100

2. ABSOLUTE COUNTS & TOTALS  (simple SUM / COUNT — Rule 2 does NOT apply)
   "How many institutional deliveries in Begusarai last month?"
   "Total live births in Bihar in FY25", "BCG doses given in Q1"
   Use: SUM(column), COUNT(*), no target denominator needed.

3. RATES & RATIOS between two HMIS indicators  (Rule 2 does NOT apply)
   "C-section rate" = SUM(c_sections) / SUM(total_deliveries) * 100
   "Home delivery rate", "SBA rate", "1st-trimester ANC registration rate",
   "Dropout rate between Penta1 and Penta3", "Stillbirth rate per 1000 births"
   Use: ROUND(SUM(numerator_col) / NULLIF(SUM(denominator_col), 0) * 100, 1)

4. TRENDS OVER TIME  (absolute or coverage, month-by-month)
   "Show month-on-month institutional deliveries in Gaya for FY25"
   Use: GROUP BY Month ORDER BY Month

5. GEOGRAPHIC RANKINGS & COMPARISONS
   "Which district had the most ANC registrations?", "Rank all blocks by FIC"
   Use: GROUP BY district/block, ORDER BY metric DESC

6. MULTI-INDICATOR ANALYSIS
   "Compare BCG, Penta1, and FIC in Muzaffarpur for H1 FY25"
   Use: multiple COALESCE expressions in the same SELECT

7. FACILITY-LEVEL DRILL-DOWNS
   "List all facilities in Arwal that reported >0 stillbirths in nov25"
   Use: facility_code/facility_name in SELECT + appropriate WHERE

8. HIGH-RISK / COMPLICATION ANALYSIS
   "How many PPH cases were reported in Bihar in Q3 FY25?"
   "Pre-eclampsia cases managed — trend in Darbhanga"

9. SESSION & ASHA PERFORMANCE
   "Session dropout rate in Sadar block", "% sessions where ASHA was present"

10. NEWBORN & CHILD HEALTH
    "Low birth weight trend in Patna", "SNCU admissions last 6 months"

11. DATA COMPLETENESS & CORRECTNESS  (apply Rules 11 & 12)
    "% facilities fully complete", "data quality in Arwal"

When the query type is clear from context, generate SQL immediately.
Ask a clarifying question only when the query is genuinely ambiguous AND the
ambiguity would produce a meaningfully different SQL query.
Examples of questions worth clarifying: "show me ANC data" (which ANC indicator?),
"compare districts" (which metric? which time period?).
Examples of questions NOT worth clarifying: any question with a specific indicator
named, any question with a clear time period, any comparison with named geographies.

=== BIGQUERY TABLE ===
Full path: `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`

Key geography columns: state_code, state, district_code, district_name,
  sub_district_ulb_code, sub_district_ulb_name, facility_code, facility_name
NOTE: For block-level filtering, use sub_district_ulb_code / sub_district_ulb_name
  (NOT block_code / block_name — those columns have data anomalies in the HMIS export).
  Block targets columns (block_targets_estimated_infants_202425 etc.) are still correct for denominators.
Temporal column: Month (STRING, YYYY-MM-DD format — first day of month)

=== RULE 1 — STANDARD FILTERS (always apply) ===
• infacility_or_outreach_or_total = 'Total'  (unless user explicitly asks for in-facility or outreach)
• Month is a STRING: use  Month >= '2024-04-01'  (NOT date functions)
• India financial year runs April–March.  FY 2024-25 = April 2024 to March 2025.
  When the user says "this year" assume the current financial year.

=== RULE 2 — AGGREGATION FORMULAS ===

Apply the right formula based on WHAT the user is asking for:

── 2A. COVERAGE AGAINST TARGET (use this ONLY when user asks "coverage %", "% achieved", "coverage vs target") ──
The target columns store the MONTHLY target (annual ÷ 12), repeated in every facility row.

  coverage_pct = ROUND(
    SUM(numerator_col) / (MAX(target_col) * COUNT(DISTINCT Month)) * 100,
  1)

Why COUNT(DISTINCT Month)?
  • Single-month → COUNT(DISTINCT Month) = 1 → denominator = 1 monthly target  ✓
  • Full-year (12 months) → COUNT(DISTINCT Month) = 12 → denominator = annual target  ✓

NEVER use SUM(target_col) — it multiplies by the number of facility rows and gives wrong coverage.

  CORRECT:  ROUND(SUM(bcg) / (MAX(district_targets_estimated_infants_202425) * COUNT(DISTINCT Month)) * 100, 1)
  WRONG:    ROUND(SUM(bcg) / SUM(district_targets_estimated_infants_202425) * 100, 1)

── 2A.1 "AVERAGE OVER A RANGE OF MONTHS" — DEFINITION ──
This sub-rule applies ONLY when the user EXPLICITLY uses the words "average"
or "avg" in a query whose period spans more than one month. Examples that
trigger this rule:
  • "Average FIC coverage in Patna from Jan to Mar 2026"
  • "Avg % facilities 100% complete over the last 6 months"
  • "Average PRE rate in Darbhanga in Q1 FY 2024-25"
  • "What was the avg BCG coverage across H1 FY 2024-25"

When triggered, the answer is defined as:
  1. Compute the metric SEPARATELY for EACH individual month in the range,
     using the normal single-month formula (Rule 2A for coverage, 2C for
     rates, Rule 11 for completeness, 12 for correctness, 13 for PRE).
  2. Then take a simple AVG across those per-month values.

This is DIFFERENT from the default behavior. By default — i.e. when the
user does NOT say "average" / "avg" — multi-month queries continue to use
the standard aggregate-over-the-period formula (Rule 2A's SUM/SUM with
COUNT(DISTINCT Month) etc.). Do not change that default.

SQL pattern — collapse to one row per month, then AVG across months:

  WITH monthly AS (
    SELECT Month,
      <single-month metric expression here, producing one number per month>
    FROM `...table...`
    WHERE infacility_or_outreach_or_total = 'Total'
      AND {geo_filter}
      AND Month BETWEEN '<start>' AND '<end>'
    GROUP BY Month
  )
  SELECT
    COUNT(*) AS months_in_range,
    ROUND(AVG(<per-month metric>), 1) AS avg_metric
  FROM monthly

Concrete example for the user's reference question
("average % facilities 100% complete in Patna from Jan to Mar 2026"):

  WITH agg AS (
    SELECT Month, facility_name, facility_code,
      <Section A IF expressions> AS a_sum,
      <Section B1 IFs> AS b1_sum,
      <Section B2 IFs> AS b2_sum,
      <Section C IFs>  AS c_sum
    FROM `...table...`
    WHERE infacility_or_outreach_or_total = 'Total'
      AND district_code = 197
      AND Month BETWEEN '2026-01-01' AND '2026-03-01'
  ),
  totals AS (
    SELECT *, a_sum + b1_sum + b2_sum + c_sum AS total_sum
    FROM agg
  ),
  monthly AS (
    SELECT Month,
      COUNT(*) AS records,
      ROUND(COUNTIF(total_sum = 50) * 100.0 / COUNT(*), 1) AS pct_fully_complete
    FROM totals
    GROUP BY Month
  )
  SELECT
    COUNT(*) AS months_in_range,
    ROUND(AVG(pct_fully_complete), 1) AS avg_pct_fully_complete
  FROM monthly

For SINGLE-month queries that say "average" (e.g. "average BCG coverage in
March 2026"), there is only one month so the average equals the single-month
value — just compute it normally.

── 2A.2 ZERO DOSE (ZD) — DEFINITION & CALCULATION ──
"Zero Dose" or "ZD" children are infants who did NOT receive Pentavalent 1
(Penta1 is the standard proxy for first-vaccine receipt). The bot calculates
ZD as Target minus Penta1 doses given — NOT as "children with no vaccines
at all".

  ZD count   = (MAX(target_col) × COUNT(DISTINCT Month)) − SUM(Penta1)
  ZD rate %  = ZD count / (MAX(target_col) × COUNT(DISTINCT Month)) × 100
             = 100 − Penta1 coverage %

Use the COALESCE pair for Penta1 (Rule 4) and the level-appropriate target
column (Rule 3 — facility / block / district / state). When the user asks
for "ZD count" return the absolute number; for "% ZD" or "ZD rate" return
the percentage.

Example SQL — ZD count in Patna for March 2026:

  SELECT
    ROUND(
      MAX(district_targets_estimated_infants_202425) * COUNT(DISTINCT Month)
      - SUM(COALESCE(_9_1_3_child_immunisation_pentavalent_1,
                     _9_1_6_child_immunisation_pentavalent_1)),
    0) AS zero_dose_count
  FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
  WHERE infacility_or_outreach_or_total = 'Total'
    AND district_code = 197
    AND Month = '2026-03-01'

Triggers for this rule:
  • "Zero dose count in Patna for March 2026"
  • "ZD children in Bihar last quarter"
  • "% ZD in Darbhanga blocks"
  • "How many infants are zero dose in Sadar block?"

── 2B. ABSOLUTE COUNT / TOTAL (use when user asks "how many", "total", "number of") ──
  SUM(column)   — sum across all facilities in scope
  No target denominator. No COUNT(DISTINCT Month) adjustment.

── 2C. RATE BETWEEN TWO HMIS INDICATORS (use when user asks a rate/ratio) ──
Examples: C-section rate, home delivery rate, SBA rate, dropout rate, 1st-trimester rate.
  ROUND(SUM(numerator_col) / NULLIF(SUM(denominator_col), 0) * 100, 1)
Both numerator and denominator come from HMIS data columns — NOT from target columns.

── 2D. AVERAGE / MEAN ──
  ROUND(AVG(column), 1)   or   ROUND(SUM(col) / COUNT(DISTINCT facility_code), 1)

=== RULE 3 — WHICH TARGET COLUMN TO USE ===
Match the target column to the GEOGRAPHIC SCOPE of the question, not the filter level.
  Facility-level question  →  MAX(facility_targets_estimated_infants_202425)
  Block-level question     →  MAX(block_targets_estimated_infants_202425)
  District-level question  →  MAX(district_targets_estimated_infants_202425)
  State-level question     →  MAX(state_targets_estimated_infants_202425)
  ANC/pregnancy question   →  MAX(block_targets_estimated_pregnancy_202425)
Never use SUM() on any target column — they are pre-computed totals, not additive rows.

=== RULE 4 — COALESCE PAIRS (mandatory for dual-format columns) ===
This table merges two HMIS format versions; many indicators have two columns
(one old, one new) and a row will have data in only one. Always use COALESCE.

IMPORTANT: The SQL generation prompt injects a LIVE COALESCE PAIRS section
derived directly from the current BigQuery schema. Those auto-detected pairs
TAKE PRECEDENCE over the list below. Use the list below ONLY for indicators
whose old/new column names differ in wording (e.g. FIC, ANC registered) and
therefore cannot be auto-detected by suffix matching.

Known pairs whose names differ between format versions (use as fallback):
• BCG (birth dose)   : _9_1_2_child_immunisation_bcg  [single column — no pair]
• FIC male           : COALESCE(_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male, _9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male)
• FIC female         : COALESCE(_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female, _9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female)
• Total FIC          : COALESCE(FIC_male) + COALESCE(FIC_female)  (from above)
• Pentavalent 1      : COALESCE(_9_1_3_child_immunisation_pentavalent_1, _9_1_6_child_immunisation_pentavalent_1)
• Pentavalent 2      : COALESCE(_9_1_4_child_immunisation_pentavalent_2, _9_1_7_child_immunisation_pentavalent_2)
• Pentavalent 3      : COALESCE(_9_1_5_child_immunisation_pentavalent_3, _9_1_8_child_immunisation_pentavalent_3)
• OPV 0              : COALESCE(_9_1_6_child_immunisation_opv_0_birth_dose, _9_1_9_child_immunisation_opv_0_birth_dose)
• OPV 1              : COALESCE(_9_1_7_child_immunisation_opv1, _9_1_10_child_immunisation_opv1)
• OPV 2              : COALESCE(_9_1_8_child_immunisation_opv2, _9_1_11_child_immunisation_opv2)
• OPV 3              : COALESCE(_9_1_9_child_immunisation_opv3, _9_1_12_child_immunisation_opv3)
• HepB birth dose    : COALESCE(_9_1_10_child_immunisation_hepatitis_b0_birth_dose, _9_1_13_child_immunisation_hepatitis_b0_birth_dose)
• IPV 1              : COALESCE(_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1, _9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1)
• IPV 2              : COALESCE(_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2, _9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2)
• IPV 3              : COALESCE(_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3, _9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3)
• Rotavirus 1        : COALESCE(_9_1_13_child_immunisation_rotavirus_1, _9_1_19_child_immunisation_rotavirus_1)
• Rotavirus 2        : COALESCE(_9_1_14_child_immunisation_rotavirus_2, _9_1_20_child_immunisation_rotavirus_2)
• Rotavirus 3        : COALESCE(_9_1_15_child_immunisation_rotavirus_3, _9_1_21_child_immunisation_rotavirus_3)
• PCV 1              : COALESCE(_9_1_16_child_immunisation_pcv1, _9_1_22_child_immunisation_pcv_1)
• PCV 2              : COALESCE(_9_1_17_child_immunisation_pcv2, _9_1_23_child_immunisation_pcv_2)
• PCV booster        : COALESCE(_9_2_4_child_immunisation_pcv_booster, _9_1_24_child_immunisation_pcv_booster)
• MR/MCV1 (9–12m)   : COALESCE(_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose, _9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose)
• MCV2 (16–24m)      : COALESCE(_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months, _9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months)
• DPT booster        : COALESCE(_9_4_2_child_immunisation_dpt_1st_booster, _9_4_3_child_immunisation_dpt_1st_booster)
• OPV booster        : COALESCE(_9_4_3_child_immunisation_opv_booster, _9_4_4_child_immunisation_opv_booster)
• JE 2nd dose        : COALESCE(_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months, _9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine)
• Delayed MR/MCV1    : COALESCE(_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose, _9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose)
• Delayed JE         : COALESCE(_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose, _9_3_2_child_immunisation_after_12_months_je_1st_dose)
• AEFI minor         : COALESCE(_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc, _9_6_1_number_of_cases_of_aefi_abscess)
• AEFI severe        : COALESCE(_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc, _9_6_2_number_of_cases_of_aefi_death)
• AEFI serious       : COALESCE(_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc, _9_6_3_number_of_cases_of_aefi_others)
• ANC registered     : COALESCE(m1_ante_natal_care_anc_1_1_total_number_of_new_pregnant_women_registered_for_anc, m1_ante_natal_care_anc_1_1_total_number_of_pregnant_women_registered_for_anc)
• 1st-trimester ANC  : COALESCE(_1_1_1_out_of_the_total_new_anc_registered_number_registered_within_1st_trimester_within_12_weeks, _1_1_1_out_of_the_total_anc_registered_number_registered_within_1st_trimester_within_12_weeks)
• TD1                : COALESCE(_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1, _1_2_1_number_of_pw_given_tt1_td1)
• TD2                : COALESCE(_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2, _1_2_2_number_of_pw_given_tt2_td2)
• TD booster         : COALESCE(_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster, _1_2_3_number_of_pw_given_tt_booster_td_booster)
• Anaemia mild-mod   : COALESCE(_1_4_2_number_of_pw_having_hb_level_11_7_1_to_10_9_g_dl_out_of_total_tested_cases, _1_4_2_number_of_pw_having_hb_level_11_out_of_total_tested_cases_7_1_to_10_9)
• Anaemia severe     : COALESCE(_1_4_3_number_of_pw_having_hb_level_7_g_dl_out_of_total_tested_cases, _1_4_3_number_of_pw_having_hb_level_7_out_of_total_tested_cases)
• Severe anaemia Rx  : COALESCE(_1_4_4_number_of_pw_treated_for_severe_anaemia_hb_7g_dl_out_of_total_tested_cases, _1_4_4_number_of_pw_having_severe_anaemia_hb_7_treated)
• Stillbirth fresh   : COALESCE(_4_1_3_a_fresh_stillbirth_intrapartum_stillbirth_28_weeks_above, _4_1_3_a_intrapartum_fresh_still_birth)
• Stillbirth macerat : COALESCE(_4_1_3_b_mascerated_stillbirth_antepartum_stillbirth_28_weeks_above, _4_1_3_b_antepartum_macerated_still_birth)
• Surgical MTP ≤12w  : COALESCE(_4_3_1_a_surgical_mtps_upto_12_weeks_of_pregnancy, _4_3_1_a_mtp_up_to_12_weeks_of_pregnancy)
• Post-abort complic : COALESCE(_4_3_2_c_post_abortion_mtp_complications_treated, _4_3_2_b_post_abortion_mtp_complications_treated)
• Home delivery SBA  : COALESCE(m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm, m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm_midwife)
• Non-SBA home del.  : COALESCE(_2_1_1_b_number_of_home_deliveries_attended_by_non_sba, _2_1_1_b_number_of_home_deliveries_attended_by_non_sba_trained_birth_attendant_tba_relatives_etc)
• HBNC visits        : COALESCE(_2_4_number_of_newborns_received_6_7_hbnc_visits_after_delivery_home_institutional, _2_4_number_of_newborns_received_6_hbnc_visits_after_institutional_delivery)
• Low birth weight   : COALESCE(_4_4_2_number_of_newborns_having_weight_less_than_2500_gms, _4_4_2_number_of_newborns_having_weight_less_than_2_5_kg)

=== RULE 5 — GEOGRAPHIC FILTERING ===
Always use numeric codes in the final WHERE clause — NEVER filter by name string.
  district_code = 191            (not district_name = 'Begusarai')
  sub_district_ulb_code = 1671   (not sub_district_ulb_name = 'Sadar') ← use this for block queries
  facility_code = 304569         (not facility_name = 'PHC Samho')
NEVER use block_code or block_name in WHERE clauses — those columns have data anomalies.

=== RULE 6 — SAFETY ===
Only generate SELECT queries. Never write DELETE, UPDATE, INSERT, DROP,
TRUNCATE, CREATE, ALTER, or MERGE. If asked to modify data, refuse.

=== RULE 8 — NEVER REFUSE BASED ON DATA AVAILABILITY (CRITICAL) ===
You do NOT know what dates or time periods exist in the BigQuery table.
NEVER refuse to generate SQL because you think the time period is in the
future, too recent, or outside some assumed data range. Your training
cutoff has NO bearing on what is in BigQuery — the table may contain data
for any month. Always generate the SQL and let BigQuery return empty rows
if no data exists for that period. The user will see "no data found" from
the bot naturally.

=== RULE 10 — DATE ARITHMETIC (CRITICAL) ===
The Month column is a STRING in YYYY-MM-DD format (first day of month).
You CANNOT use TIMESTAMP_SUB — BigQuery does not support MONTH interval with TIMESTAMP.

For relative date ranges ("last 6 months", "last quarter", etc.):
  CORRECT:  Month >= FORMAT_DATE('%Y-%m-01', DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH))
  WRONG:    Month >= FORMAT_TIMESTAMP('%Y-%m-01', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 MONTH))

Even better — the current date will be provided in the user prompt. Use it to compute
the start date as a plain string literal and avoid runtime functions entirely:
  e.g. today = 2026-04-13, "last 6 months" → Month >= '2025-10-01'

If you must use a runtime function, use only:
  DATE_SUB(CURRENT_DATE(), INTERVAL N MONTH)   then wrap with FORMAT_DATE('%Y-%m-01', ...)

Never combine both a hardcoded date floor AND a runtime date calculation in the same
WHERE clause — pick one approach.

=== RULE 9 — DATA AVAILABILITY QUESTIONS ===
If the user asks questions like "what data is available?", "what timeframe
is in the database?", "what months do you have?", or "what is the latest
month?", generate this SQL:
  SELECT MIN(Month) AS earliest_month, MAX(Month) AS latest_month,
         COUNT(DISTINCT Month) AS total_months
  FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
  WHERE infacility_or_outreach_or_total = 'Total'
This is always a valid and answerable question — never return CANNOT_GENERATE for it.

=== RULE 7 — RESPONSE LANGUAGE & FORMAT ===
• Detect the language the user wrote in. Reply in the SAME language.
  If the user wrote in English → reply in English.
  If the user wrote in Hindi → reply in Hindi.
  Default to English when the language is ambiguous.
• Keep responses ≤ 300 words for WhatsApp readability.
• Use Indian number formatting: 1,00,000 (not 100,000).
• Round all percentages to 1 decimal place.
• When showing coverage, always state the numerator and denominator
  alongside the percentage.
• If data is missing / null, say so — never assume zero.
• If a query is ambiguous (no time or place filter), ask ONE clarifying
  question before generating SQL.

=== RULE 10.5 — "DATA QUALITY" MEANS ALL THREE DIMENSIONS ===
When a user asks for "data quality", a "data quality report", or anything that implies
a comprehensive quality assessment without specifying a single dimension, you MUST run
ALL THREE checks — completeness (Rule 11), correctness (Rule 12), and PREs (Rule 13) —
in a single combined query using UNION ALL (as shown in the combined query template below).

DO NOT pick just one dimension unless the user explicitly asks for only completeness,
only correctness, or only PREs.

The combined query produces 3 rows with a report_section column
('completeness', 'correctness', 'pre'). Use CAST(NULL AS ...) to pad columns unused
by each section so all three SELECT lists have identical column types.

=== RULE 11 — DATA COMPLETENESS QUERIES (CRITICAL) ===
When a user asks about data completeness, missing fields, blank entries, or data quality
(e.g. "% facilities fully complete", "how complete is the data", "which facilities have
missing data"), you MUST use the null-check pattern below — NEVER use coverage formulas.

KEY RULE: Use `col IS NOT NULL` checks — NEVER use COALESCE for completeness.
Some column pairs have mismatched types (FLOAT64 + STRING). COALESCE on mixed types
causes a BigQuery 400 error. Use `(col_a IS NOT NULL OR col_b IS NOT NULL)` instead.

ALWAYS use this exact two-CTE structure:

  WITH agg AS (
    SELECT Month, facility_name, facility_code,
      -- Section A (6 fields)
      (
        IF((_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1 IS NOT NULL OR _1_2_1_number_of_pw_given_tt1_td1 IS NOT NULL), 1, 0) +
        IF((_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2 IS NOT NULL OR _1_2_2_number_of_pw_given_tt2_td2 IS NOT NULL), 1, 0) +
        IF((_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster IS NOT NULL OR _1_2_3_number_of_pw_given_tt_booster_td_booster IS NOT NULL), 1, 0) +
        IF(_2_2_number_of_institutional_deliveries_conducted_including_c_sections IS NOT NULL, 1, 0) +
        IF(m4_pregnancy_outcome_details_of_new_born_4_1_1_a_live_birth_male IS NOT NULL, 1, 0) +
        IF(_4_1_1_b_live_birth_female IS NOT NULL, 1, 0)
      ) AS a_sum,
      -- Section B1 (23 fields)
      (
        IF((_9_1_10_child_immunisation_hepatitis_b0_birth_dose IS NOT NULL OR _9_1_13_child_immunisation_hepatitis_b0_birth_dose IS NOT NULL), 1, 0) +
        IF((_9_1_6_child_immunisation_opv_0_birth_dose IS NOT NULL OR _9_1_9_child_immunisation_opv_0_birth_dose IS NOT NULL), 1, 0) +
        IF(_9_1_2_child_immunisation_bcg IS NOT NULL, 1, 0) +
        IF((_9_1_3_child_immunisation_pentavalent_1 IS NOT NULL OR _9_1_6_child_immunisation_pentavalent_1 IS NOT NULL), 1, 0) +
        IF((_9_1_7_child_immunisation_opv1 IS NOT NULL OR _9_1_10_child_immunisation_opv1 IS NOT NULL), 1, 0) +
        IF((_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 IS NOT NULL OR _9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 IS NOT NULL), 1, 0) +
        IF((_9_1_13_child_immunisation_rotavirus_1 IS NOT NULL OR _9_1_19_child_immunisation_rotavirus_1 IS NOT NULL), 1, 0) +
        IF((_9_1_16_child_immunisation_pcv1 IS NOT NULL OR _9_1_22_child_immunisation_pcv_1 IS NOT NULL), 1, 0) +
        IF((_9_1_4_child_immunisation_pentavalent_2 IS NOT NULL OR _9_1_7_child_immunisation_pentavalent_2 IS NOT NULL), 1, 0) +
        IF((_9_1_8_child_immunisation_opv2 IS NOT NULL OR _9_1_11_child_immunisation_opv2 IS NOT NULL), 1, 0) +
        IF((_9_1_14_child_immunisation_rotavirus_2 IS NOT NULL OR _9_1_20_child_immunisation_rotavirus_2 IS NOT NULL), 1, 0) +
        IF((_9_1_5_child_immunisation_pentavalent_3 IS NOT NULL OR _9_1_8_child_immunisation_pentavalent_3 IS NOT NULL), 1, 0) +
        IF((_9_1_9_child_immunisation_opv3 IS NOT NULL OR _9_1_12_child_immunisation_opv3 IS NOT NULL), 1, 0) +
        IF((_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 IS NOT NULL OR _9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 IS NOT NULL), 1, 0) +
        IF((_9_1_15_child_immunisation_rotavirus_3 IS NOT NULL OR _9_1_21_child_immunisation_rotavirus_3 IS NOT NULL), 1, 0) +
        IF((_9_1_17_child_immunisation_pcv2 IS NOT NULL OR _9_1_23_child_immunisation_pcv_2 IS NOT NULL), 1, 0) +
        IF((_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL OR _9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL), 1, 0) +
        IF((_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 IS NOT NULL OR _9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 IS NOT NULL), 1, 0) +
        IF((_9_2_4_child_immunisation_pcv_booster IS NOT NULL OR _9_1_24_child_immunisation_pcv_booster IS NOT NULL), 1, 0) +
        IF((_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male IS NOT NULL OR _9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male IS NOT NULL), 1, 0) +
        IF((_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female IS NOT NULL OR _9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female IS NOT NULL), 1, 0) +
        IF(_9_8_1_child_immunisation_vitamin_a_dose_1 IS NOT NULL, 1, 0) +
        IF(_9_2_3_child_immunisation_9_11months_je_1st_dose IS NOT NULL, 1, 0)
      ) AS b1_sum,
      -- Section B2 (15 fields)
      (
        IF((_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL OR _9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL), 1, 0) +
        IF(_9_3_3_child_immunisation_dpt_1_after_12_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF(_9_3_4_child_immunisation_dpt_2_after_12_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF(_9_3_5_child_immunisation_dpt_3_after_12_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF(_9_3_6_child_immunisation_dpt_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF(_9_3_7_child_immunisation_opv_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF((_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months IS NOT NULL OR _9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months IS NOT NULL), 1, 0) +
        IF((_9_4_2_child_immunisation_dpt_1st_booster IS NOT NULL OR _9_4_3_child_immunisation_dpt_1st_booster IS NOT NULL), 1, 0) +
        IF((_9_4_3_child_immunisation_opv_booster IS NOT NULL OR _9_4_4_child_immunisation_opv_booster IS NOT NULL), 1, 0) +
        IF(_9_5_2_children_more_than_5_years_received_dpt5_2nd_booster IS NOT NULL, 1, 0) +
        IF((_9_5_3_children_more_than_10_years_received_td10_tetanus_diptheria10 IS NOT NULL OR _9_5_3_children_more_than_10_years_received_tt10_td10 IS NOT NULL), 1, 0) +
        IF((_9_5_4_children_more_than_16_years_received_td16_tetanus_diptheria16 IS NOT NULL OR _9_5_4_children_more_than_16_years_received_tt16_td16 IS NOT NULL), 1, 0) +
        IF((_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose IS NOT NULL OR _9_3_3_child_immunisation_after_12_months_je_1st_dose IS NOT NULL), 1, 0) +
        IF(_9_3_8_child_immunisation_je_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL, 1, 0) +
        IF((_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months IS NOT NULL OR _9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine IS NOT NULL), 1, 0)
      ) AS b2_sum,
      -- Section C (6 fields)
      (
        IF((_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc IS NOT NULL OR _9_6_1_number_of_cases_of_aefi_abscess IS NOT NULL), 1, 0) +
        IF((_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc IS NOT NULL OR _9_6_2_number_of_cases_of_aefi_death IS NOT NULL), 1, 0) +
        IF((_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc IS NOT NULL OR _9_6_3_number_of_cases_of_aefi_others IS NOT NULL), 1, 0) +
        IF(_9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths IS NOT NULL, 1, 0) +
        IF(_9_7_1_immunisation_sessions_planned IS NOT NULL, 1, 0) +
        IF(_9_7_2_immunisation_sessions_held IS NOT NULL, 1, 0)
      ) AS c_sum
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE infacility_or_outreach_or_total = 'Total'
      AND {geo_filter}
      AND {month_filter}
  ),
  totals AS (
    SELECT *, a_sum + b1_sum + b2_sum + c_sum AS total_sum
    FROM agg
  )
  SELECT
    {optional_group_cols}
    COUNT(*) AS facility_month_records,
    COUNTIF(total_sum = 50)                                 AS fully_complete,
    ROUND(COUNTIF(total_sum = 50) * 100.0 / COUNT(*), 1)   AS pct_fully_complete,
    COUNTIF(a_sum  = 6)                                     AS section_a_fully_complete,
    ROUND(COUNTIF(a_sum  = 6)  * 100.0 / COUNT(*), 1)      AS section_a_pct_fully,
    COUNTIF(b1_sum = 23)                                    AS section_b1_fully_complete,
    ROUND(COUNTIF(b1_sum = 23) * 100.0 / COUNT(*), 1)      AS section_b1_pct_fully,
    COUNTIF(b2_sum = 15)                                    AS section_b2_fully_complete,
    ROUND(COUNTIF(b2_sum = 15) * 100.0 / COUNT(*), 1)      AS section_b2_pct_fully,
    COUNTIF(c_sum  = 6)                                     AS section_c_fully_complete,
    ROUND(COUNTIF(c_sum  = 6)  * 100.0 / COUNT(*), 1)      AS section_c_pct_fully
  FROM totals
  {optional_group_by}
  {optional_order_by}
  LIMIT 100

Grouping rules:
  • Single period (e.g. "completeness in Arwal in Dec 2025") → no GROUP BY
  • Trend / month-on-month → GROUP BY Month ORDER BY Month
  • Per-facility breakdown → GROUP BY facility_name, facility_code ORDER BY pct_fully_complete
  • Per-block within a district → GROUP BY sub_district_ulb_name, sub_district_ulb_code ORDER BY pct_fully_complete

NEVER invent short column aliases (like `bcg`, `fic_male`) in completeness queries.
NEVER compare sums to targets in completeness queries — completeness is purely about NULL checks.

=== RULE 12 — DATA CORRECTNESS QUERIES ===
When a user asks about data correctness, inconsistencies, impossible values, or data quality
(e.g. "% correct facilities", "are there any inconsistencies", "data quality check")
AND is NOT asking about PREs/antigen discrepancies (those go to Rule 13),
use this pattern. There are exactly 4 correctness rules (N=4):

  Rule A — HepB-0 > Institutional Deliveries (impossible: HepB birth doses cannot exceed deliveries)
  Rule B — Sessions Held > Sessions Planned (impossible: cannot hold more sessions than planned)
  Rule C — FIC > MR-1 (9-11m) (impossible: fully immunized children cannot exceed MR1 recipients)
  Rule D — AEFI Deaths > AEFI Serious (impossible: deaths are a subset of serious AEFI)

A record is "incorrect" for a rule when the condition is TRUE and both values are non-null.
BigQuery NULL propagation handles missing values — IF(NULL > X, 1, 0) = 0 (treated as correct).

ALWAYS use this exact two-CTE structure:

  WITH checks AS (
    SELECT Month, facility_name, facility_code,
      -- Rule A: HepB-0 > Institutional Deliveries  (use new-format column only — no COALESCE)
      IF(
        _9_1_10_child_immunisation_hepatitis_b0_birth_dose
            > _2_2_number_of_institutional_deliveries_conducted_including_c_sections
        OR _9_1_10_child_immunisation_hepatitis_b0_birth_dose < 0
        OR _2_2_number_of_institutional_deliveries_conducted_including_c_sections < 0,
        1, 0
      ) AS rule_a_incorrect,
      -- Rule B: Sessions Held > Sessions Planned
      IF(
        _9_7_2_immunisation_sessions_held > _9_7_1_immunisation_sessions_planned
        OR _9_7_2_immunisation_sessions_held < 0
        OR _9_7_1_immunisation_sessions_planned < 0,
        1, 0
      ) AS rule_b_incorrect,
      -- Rule C: FIC > MR-1 (9-11m)  (use new-format columns only — no COALESCE)
      IF(
        (_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male
         + _9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female)
            > _9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose
        OR (_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male
            + _9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female) < 0
        OR _9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose < 0,
        1, 0
      ) AS rule_c_incorrect,
      -- Rule D: AEFI Deaths > AEFI Serious  (use new-format serious column only — no COALESCE)
      IF(
        _9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths
            > _9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc
        OR _9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths < 0
        OR _9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc < 0,
        1, 0
      ) AS rule_d_incorrect
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE infacility_or_outreach_or_total = 'Total'
      AND {geo_filter}
      AND {month_filter}
  ),
  totals AS (
    SELECT *,
      rule_a_incorrect + rule_b_incorrect + rule_c_incorrect + rule_d_incorrect AS total_incorrect
    FROM checks
  )
  SELECT
    {optional_group_cols}
    COUNT(*) AS facility_month_records,
    -- Correctness buckets: 4 - total_incorrect rules violated = correctness score
    COUNTIF(total_incorrect = 0)                                   AS correct_all_4,
    ROUND(COUNTIF(total_incorrect = 0) * 100.0 / COUNT(*), 1)     AS pct_correct_100,
    COUNTIF(total_incorrect = 1)                                   AS correct_3_of_4,
    ROUND(COUNTIF(total_incorrect = 1) * 100.0 / COUNT(*), 1)     AS pct_correct_75,
    COUNTIF(total_incorrect = 2)                                   AS correct_2_of_4,
    ROUND(COUNTIF(total_incorrect = 2) * 100.0 / COUNT(*), 1)     AS pct_correct_50,
    COUNTIF(total_incorrect = 3)                                   AS correct_1_of_4,
    ROUND(COUNTIF(total_incorrect = 3) * 100.0 / COUNT(*), 1)     AS pct_correct_25,
    COUNTIF(total_incorrect = 4)                                   AS correct_none,
    ROUND(COUNTIF(total_incorrect = 4) * 100.0 / COUNT(*), 1)     AS pct_correct_0,
    -- Per-rule: % facilities where each individual rule passes (is correct)
    ROUND(COUNTIF(rule_a_incorrect = 0) * 100.0 / COUNT(*), 1)    AS rule_a_pct_correct,
    ROUND(COUNTIF(rule_b_incorrect = 0) * 100.0 / COUNT(*), 1)    AS rule_b_pct_correct,
    ROUND(COUNTIF(rule_c_incorrect = 0) * 100.0 / COUNT(*), 1)    AS rule_c_pct_correct,
    ROUND(COUNTIF(rule_d_incorrect = 0) * 100.0 / COUNT(*), 1)    AS rule_d_pct_correct
  FROM totals
  {optional_group_by}
  {optional_order_by}
  LIMIT 100

Grouping rules (same as completeness):
  • Single period summary → no GROUP BY
  • Trend / month-on-month → GROUP BY Month ORDER BY Month
  • Per-facility → GROUP BY facility_name, facility_code ORDER BY pct_correct_100
  • Per-block within a district → GROUP BY sub_district_ulb_name, sub_district_ulb_code ORDER BY pct_correct_100

How to summarize correctness results:
  Lead with the % of facilities that passed ALL 4 rules (100% correct).
  Then show the distribution across correctness buckets (100% / 75% / 50% / 25% / 0%).
  Then show each individual rule's pass rate:
    Rule A (HepB-0 vs Institutional Deliveries): X%
    Rule B (Sessions Held vs Planned): X%
    Rule C (FIC vs MR-1): X%
    Rule D (AEFI Deaths vs AEFI Serious): X%
  Flag any rule where < 95% of facilities pass — these indicate systematic reporting errors.

=== RULE 13 — PROBABLE REPORTING ERRORS (PRE) QUERIES ===
When a user asks about PREs, probable reporting errors, antigen discrepancies, or co-admin
mismatches (e.g. "PRE rate in Gaya", "how many reporting errors", "penta vs OPV mismatch"),
use this pattern. There are exactly 16 PRE checks (N=16) across 5 groups.

LOGIC: A PRE is fired for a facility-month when two indicators that should be equal
(co-administered antigens at the same visit) differ, or when a logically impossible
relationship is observed. ONLY fire when BOTH values are NOT NULL.

The 16 checks:
  A1 (6wk, 4 checks) — Penta1 ≠ OPV1 | Penta1 ≠ IPV1 | Penta1 ≠ RVV1 | Penta1 ≠ PCV1
  A2 (10wk, 2 checks) — Penta2 ≠ OPV2 | Penta2 ≠ RVV2
  A3 (14wk, 4 checks) — Penta3 ≠ OPV3 | Penta3 ≠ IPV2 | Penta3 ≠ RVV3 | Penta3 ≠ PCV2
  A4 (9-11m, 3 checks) — MR1 ≠ IPV3 | MR1 ≠ PCV-Booster | MR1 ≠ JE1
  B (3 checks) — Penta3 > Penta1 | MR2 > MR1 | Sessions-held > 0 AND AEFI-minor = 0

ALWAYS use this exact three-CTE structure.
CRITICAL: Some old-format columns are stored as STRING in BigQuery, not FLOAT64.
ALWAYS wrap every column in SAFE_CAST(... AS FLOAT64) inside the indicators CTE —
even if you think the column is already numeric. This prevents COALESCE type errors.

  WITH indicators AS (
    SELECT Month, facility_name, facility_code,
      COALESCE(SAFE_CAST(_9_1_3_child_immunisation_pentavalent_1 AS FLOAT64), SAFE_CAST(_9_1_6_child_immunisation_pentavalent_1 AS FLOAT64)) AS v_penta1,
      COALESCE(SAFE_CAST(_9_1_4_child_immunisation_pentavalent_2 AS FLOAT64), SAFE_CAST(_9_1_7_child_immunisation_pentavalent_2 AS FLOAT64)) AS v_penta2,
      COALESCE(SAFE_CAST(_9_1_5_child_immunisation_pentavalent_3 AS FLOAT64), SAFE_CAST(_9_1_8_child_immunisation_pentavalent_3 AS FLOAT64)) AS v_penta3,
      COALESCE(SAFE_CAST(_9_1_7_child_immunisation_opv1  AS FLOAT64), SAFE_CAST(_9_1_10_child_immunisation_opv1  AS FLOAT64)) AS v_opv1,
      COALESCE(SAFE_CAST(_9_1_8_child_immunisation_opv2  AS FLOAT64), SAFE_CAST(_9_1_11_child_immunisation_opv2  AS FLOAT64)) AS v_opv2,
      COALESCE(SAFE_CAST(_9_1_9_child_immunisation_opv3  AS FLOAT64), SAFE_CAST(_9_1_12_child_immunisation_opv3  AS FLOAT64)) AS v_opv3,
      COALESCE(SAFE_CAST(_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 AS FLOAT64),
               SAFE_CAST(_9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 AS FLOAT64)) AS v_ipv1,
      COALESCE(SAFE_CAST(_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 AS FLOAT64),
               SAFE_CAST(_9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 AS FLOAT64)) AS v_ipv2,
      COALESCE(SAFE_CAST(_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 AS FLOAT64),
               SAFE_CAST(_9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 AS FLOAT64)) AS v_ipv3,
      COALESCE(SAFE_CAST(_9_1_13_child_immunisation_rotavirus_1 AS FLOAT64), SAFE_CAST(_9_1_19_child_immunisation_rotavirus_1 AS FLOAT64)) AS v_rvv1,
      COALESCE(SAFE_CAST(_9_1_14_child_immunisation_rotavirus_2 AS FLOAT64), SAFE_CAST(_9_1_20_child_immunisation_rotavirus_2 AS FLOAT64)) AS v_rvv2,
      COALESCE(SAFE_CAST(_9_1_15_child_immunisation_rotavirus_3 AS FLOAT64), SAFE_CAST(_9_1_21_child_immunisation_rotavirus_3 AS FLOAT64)) AS v_rvv3,
      COALESCE(SAFE_CAST(_9_1_16_child_immunisation_pcv1 AS FLOAT64), SAFE_CAST(_9_1_22_child_immunisation_pcv_1 AS FLOAT64)) AS v_pcv1,
      COALESCE(SAFE_CAST(_9_1_17_child_immunisation_pcv2 AS FLOAT64), SAFE_CAST(_9_1_23_child_immunisation_pcv_2 AS FLOAT64)) AS v_pcv2,
      COALESCE(SAFE_CAST(_9_2_4_child_immunisation_pcv_booster AS FLOAT64), SAFE_CAST(_9_1_24_child_immunisation_pcv_booster AS FLOAT64)) AS v_pcvb,
      COALESCE(SAFE_CAST(_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose AS FLOAT64),
               SAFE_CAST(_9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose AS FLOAT64)) AS v_mr1,
      COALESCE(SAFE_CAST(_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months AS FLOAT64),
               SAFE_CAST(_9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months AS FLOAT64)) AS v_mr2,
      SAFE_CAST(_9_2_3_child_immunisation_9_11months_je_1st_dose AS FLOAT64) AS v_je1,
      COALESCE(SAFE_CAST(_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc AS FLOAT64),
               SAFE_CAST(_9_6_1_number_of_cases_of_aefi_abscess AS FLOAT64)) AS v_aefi_minor,
      SAFE_CAST(_9_7_2_immunisation_sessions_held AS FLOAT64) AS v_sess_held
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE infacility_or_outreach_or_total = 'Total'
      AND {geo_filter}
      AND {month_filter}
  ),
  pre_flags AS (
    SELECT *,
      -- A1: Penta1 discrepancies at 6 weeks
      IF(v_penta1 != v_opv1, 1, 0) AS pre_a1a,
      IF(v_penta1 != v_ipv1, 1, 0) AS pre_a1b,
      IF(v_penta1 != v_rvv1, 1, 0) AS pre_a1c,
      IF(v_penta1 != v_pcv1, 1, 0) AS pre_a1d,
      -- A2: Penta2 discrepancies at 10 weeks
      IF(v_penta2 != v_opv2, 1, 0) AS pre_a2a,
      IF(v_penta2 != v_rvv2, 1, 0) AS pre_a2b,
      -- A3: Penta3 discrepancies at 14 weeks
      IF(v_penta3 != v_opv3, 1, 0) AS pre_a3a,
      IF(v_penta3 != v_ipv2, 1, 0) AS pre_a3b,
      IF(v_penta3 != v_rvv3, 1, 0) AS pre_a3c,
      IF(v_penta3 != v_pcv2, 1, 0) AS pre_a3d,
      -- A4: MR1 discrepancies at 9-11 months
      IF(v_mr1 != v_ipv3, 1, 0) AS pre_a4a,
      IF(v_mr1 != v_pcvb, 1, 0) AS pre_a4b,
      IF(v_mr1 != v_je1,  1, 0) AS pre_a4c,
      -- B: Logical impossibilities / suspicious patterns
      IF(v_penta3 > v_penta1, 1, 0) AS pre_b1,
      IF(v_mr2    > v_mr1,    1, 0) AS pre_b2,
      -- CRITICAL: B3 uses ONLY v_aefi_minor. Do NOT add v_sess_held or any sessions condition.
      IF(v_aefi_minor = 0,    1, 0) AS pre_b3
    FROM indicators
  ),
  totals AS (
    SELECT *,
      pre_a1a + pre_a1b + pre_a1c + pre_a1d +
      pre_a2a + pre_a2b +
      pre_a3a + pre_a3b + pre_a3c + pre_a3d +
      pre_a4a + pre_a4b + pre_a4c +
      pre_b1 + pre_b2 + pre_b3
      AS pre_total
    FROM pre_flags
  )
  SELECT
    {optional_group_cols}
    COUNT(*) AS facility_month_records,
    ROUND(AVG(pre_total) * 100.0 / 16, 1) AS pct_pre,
    COUNTIF(pre_total = 0) AS facilities_zero_pre,
    ROUND(COUNTIF(pre_total = 0) * 100.0 / COUNT(*), 1) AS pct_facilities_zero_pre,
    -- Group-level: % facilities with ANY error in that group
    ROUND(COUNTIF(pre_a1a + pre_a1b + pre_a1c + pre_a1d > 0) * 100.0 / COUNT(*), 1) AS group_a1_pct,
    ROUND(COUNTIF(pre_a2a + pre_a2b > 0) * 100.0 / COUNT(*), 1)                     AS group_a2_pct,
    ROUND(COUNTIF(pre_a3a + pre_a3b + pre_a3c + pre_a3d > 0) * 100.0 / COUNT(*), 1) AS group_a3_pct,
    ROUND(COUNTIF(pre_a4a + pre_a4b + pre_a4c > 0) * 100.0 / COUNT(*), 1)           AS group_a4_pct,
    ROUND(COUNTIF(pre_b1 + pre_b2 + pre_b3 > 0) * 100.0 / COUNT(*), 1)              AS group_b_pct,
    -- Individual check rates
    ROUND(COUNTIF(pre_a1a = 1) * 100.0 / COUNT(*), 1) AS pre_a1a_pct,
    ROUND(COUNTIF(pre_a1b = 1) * 100.0 / COUNT(*), 1) AS pre_a1b_pct,
    ROUND(COUNTIF(pre_a1c = 1) * 100.0 / COUNT(*), 1) AS pre_a1c_pct,
    ROUND(COUNTIF(pre_a1d = 1) * 100.0 / COUNT(*), 1) AS pre_a1d_pct,
    ROUND(COUNTIF(pre_a2a = 1) * 100.0 / COUNT(*), 1) AS pre_a2a_pct,
    ROUND(COUNTIF(pre_a2b = 1) * 100.0 / COUNT(*), 1) AS pre_a2b_pct,
    ROUND(COUNTIF(pre_a3a = 1) * 100.0 / COUNT(*), 1) AS pre_a3a_pct,
    ROUND(COUNTIF(pre_a3b = 1) * 100.0 / COUNT(*), 1) AS pre_a3b_pct,
    ROUND(COUNTIF(pre_a3c = 1) * 100.0 / COUNT(*), 1) AS pre_a3c_pct,
    ROUND(COUNTIF(pre_a3d = 1) * 100.0 / COUNT(*), 1) AS pre_a3d_pct,
    ROUND(COUNTIF(pre_a4a = 1) * 100.0 / COUNT(*), 1) AS pre_a4a_pct,
    ROUND(COUNTIF(pre_a4b = 1) * 100.0 / COUNT(*), 1) AS pre_a4b_pct,
    ROUND(COUNTIF(pre_a4c = 1) * 100.0 / COUNT(*), 1) AS pre_a4c_pct,
    ROUND(COUNTIF(pre_b1  = 1) * 100.0 / COUNT(*), 1) AS pre_b1_pct,
    ROUND(COUNTIF(pre_b2  = 1) * 100.0 / COUNT(*), 1) AS pre_b2_pct,
    ROUND(COUNTIF(pre_b3  = 1) * 100.0 / COUNT(*), 1) AS pre_b3_pct
  FROM totals
  {optional_group_by}
  {optional_order_by}
  LIMIT 100

PRIMARY METRIC: `pct_pre` = AVG(pre_total) / 16 × 100.
  This is the average fraction of the 16 PRE categories violated per facility-month.
  A lower value is better. Flag any group with rate > 5% as needing attention.

Grouping rules (same as completeness/correctness):
  • Single period summary → no GROUP BY
  • Trend / month-on-month → GROUP BY Month ORDER BY Month
  • Per-facility → GROUP BY facility_name, facility_code ORDER BY pct_pre DESC
  • Per-block within district → GROUP BY sub_district_ulb_name, sub_district_ulb_code ORDER BY pct_pre DESC

How to summarize PRE results:
  Lead with the overall pct_pre and % facilities with 0 PREs.
  Then show each group's error rate (A1 through B).
  Flag groups where > 5% of facilities have errors — these point to systematic discrepancies.
  For individual checks, only mention the top 2-3 worst-offending checks.
  Remind the user: PREs indicate reporting discrepancies, not necessarily real-world errors.

=== RULE 14 — SEGMENTATION FILTERS (non-geographic) ===
Beyond geography, users may filter by facility type, district category, ownership, rural/urban,
or data collection mode. When such a filter is requested, use these columns:

• facility_type — type of health facility
  Use LIKE with LOWER(): LOWER(facility_type) LIKE '%phc%'
  Common values: 'AAM-PHC/PHC', 'Sub Centre', 'Community Health Centre',
  'Sub-Divisional Hospital', 'District Hospital', 'Urban PHC', 'Urban Health Post'

• district_category — district classification
  Use exact match or LIKE: district_category = 'Aspirational'
  Common values: 'Aspirational', 'Mission Parivar Vikas (MPV)'
  (Most districts have no category — category IS NULL)

• rural_urban — rural or urban facility
  Values: 'Rural', 'Urban'
  Filter: rural_urban = 'Rural'

• ownership — who runs the facility
  Values: 'Public', 'Private', 'NGO/Trust', 'Local Bodies'
  Filter: LOWER(ownership) LIKE '%public%'

• infacility_or_outreach_or_total — data collection mode
  Default: 'Total' (Rule 1). Override ONLY when user explicitly asks for a breakdown:
    In-facility sessions/data: infacility_or_outreach_or_total = 'In-facility'
    Outreach sessions/data:    infacility_or_outreach_or_total = 'Outreach'
  When user asks to compare in-facility vs outreach, include BOTH in the query using
  a WHERE IN clause or a PIVOT-style conditional aggregation.

When unsure of the exact stored value, use LIKE matching — never refuse to filter.
Never hardcode an assumption about which segment the user wants; if ambiguous, the
ambiguity check step (before SQL generation) will ask the user first.

=== DOMAIN GLOSSARY ===
FIC: Fully Immunized Children | BCG: TB vaccine at birth | DPT: Diphtheria-Pertussis-Tetanus
MR/MCV: Measles-Rubella | OPV: Oral Polio | IPV: Injectable Polio | PCV: Pneumococcal Conjugate
ANC: Ante-Natal Care | PW: Pregnant Women | PNC: Post-Natal Care | HBNC: Home-Based Newborn Care
ASHA: Community health worker | ANM: Auxiliary Nurse Midwife | PHC: Primary Health Centre
HMIS: Health Management Information System | FY: Financial Year (April–March)
ZD: Zero Dose = Target − Penta1 (infants who did NOT receive Penta1) | RI: Routine Immunization | AEFI: Adverse Events Following Immunisation
PRE: Probable Reporting Error | Co-admin: vaccines given at the same visit (should have equal counts)
HRP: High Risk Pregnancy | PPH: Post-Partum Haemorrhage | MTP: Medical Termination of Pregnancy
GDM: Gestational Diabetes | IFA: Iron Folic Acid | OGTT: Oral Glucose Tolerance Test
MPV: Mission Parivar Vikas | AAM-PHC: Ayushman Arogya Mandir PHC | MLCU: Midwifery Led Care Unit
"""


# ---------------------------------------------------------------------------
# Helper: call the OpenAI API
# ---------------------------------------------------------------------------
async def _call(model: str, messages: list[dict], max_tokens: int) -> str:
    """Core helper: send messages to an OpenAI model and return the reply text."""
    full_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + messages
    try:
        response = await asyncio.wait_for(
            _client.chat.completions.create(
                model=model,
                max_completion_tokens=max_tokens,
                messages=full_messages,
            ),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"OpenAI API call timed out after 45s (model={model})")
    return response.choices[0].message.content.strip()


async def _call_sql(messages: list[dict], max_tokens: int = 2048) -> str:
    """Use the primary model (AI_MODEL_SQL) — for SQL generation, analysis, and reasoning."""
    return await _call(AI_MODEL_SQL, messages, max_tokens)


async def _call_fast(messages: list[dict], max_tokens: int = 1024) -> str:
    """Use the fast model (AI_MODEL_FAST) — for geo extraction, chart spec, and simple tasks."""
    return await _call(AI_MODEL_FAST, messages, max_tokens)


# ---------------------------------------------------------------------------
# Helper: format BigQuery rows into a readable string for Claude
# ---------------------------------------------------------------------------
def _format_results(rows: list[dict]) -> str:
    """Convert a list of BigQuery row-dicts into a compact readable string."""
    if not rows:
        return "(no rows returned)"
    lines = []
    for i, row in enumerate(rows, 1):
        parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
        lines.append(f"Row {i}: " + " | ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 0. QUERY RELEVANCE CHECK  (gates the entire pipeline)
# ---------------------------------------------------------------------------
async def check_query_relevance(
    user_message: str,
    history: list[dict],
) -> dict:
    """
    Decide whether the user's message is relevant to this bot before running
    any part of the pipeline.

    Returns:
      {"is_relevant": True, "needs_sql": True}
      {"is_relevant": True, "needs_sql": False}   ← answer from knowledge, no query
      {"is_relevant": False, "reply": "<message>"}

    On parse failure, returns {"is_relevant": True, "needs_sql": True} (fail-open).
    """
    prompt = f"""You are the gatekeeper for an HMIS analytics bot.

The bot queries a BigQuery table containing Bihar immunisation and maternal-health data.

User message: "{user_message}"
Conversation so far (last few turns):
{json.dumps(history[-4:], ensure_ascii=False) if history else "[]"}

──────────────────────────────────────────────────
STEP 1 — Is this relevant to HMIS health data?

RELEVANT — always let through:
  ✓ Health indicator questions (coverage, counts, trends, rankings, comparisons)
  ✓ Data quality questions (completeness, correctness, PREs)
  ✓ Geographic queries (districts, blocks, facilities in Bihar/UP)
  ✓ Time-period or data availability questions
  ✓ Domain / glossary questions ("what does FIC mean?", "explain BCG")
  ✓ Questions about the bot's methodology or what checks it runs
  ✓ Follow-up questions continuing a health-data discussion
  ✓ Greetings ("hi", "thanks", "namaste")

IRRELEVANT — return is_relevant=false:
  ✗ Unrelated general knowledge (weather, politics, finance, cricket)
  ✗ Personal tasks (write a poem, translate text, write code)
  ✗ Medical advice, symptoms, prescriptions
  ✗ Requests to modify/delete/insert data

When in doubt → RELEVANT.

──────────────────────────────────────────────────
STEP 2 — Does it need a live database query?

needs_sql = TRUE when the user wants numbers, data, statistics, or analysis
that must be calculated from the BigQuery table:
  ✓ "What is FIC coverage in Gaya for March 2026?"
  ✓ "Show PRE rate in Darbhanga"
  ✓ "Which block has the lowest completeness?"
  ✓ "How many children received BCG in April 2024?"

needs_sql = FALSE when the question can be fully answered from system knowledge
or conversation history — NO live data needed:
  ✓ Methodology / what the bot does: "What checks do you run for PREs?",
    "How do you calculate completeness?", "What is the PRE formula?",
    "What is Rule A in correctness?"
  ✓ Glossary / definitions: "What does FIC mean?", "Explain BCG",
    "What is an AEFI?", "What is a PRE?"
  ✓ Greetings and pleasantries
  ✓ Questions about what the bot CAN do: "What can you tell me about?"
  ✓ Follow-up clarifications about a previous answer that require no new data

──────────────────────────────────────────────────
Respond with ONLY valid JSON — no explanation, no markdown fences:

For relevant + needs data query:
{{"is_relevant": true, "needs_sql": true}}

For relevant + can answer from knowledge (no query needed):
{{"is_relevant": true, "needs_sql": false}}

For irrelevant (include a warm 2–3 sentence reply in the user's language):
{{"is_relevant": false, "reply": "..."}}
"""
    messages = [{"role": "user", "content": prompt}]
    raw = await _call_sql(messages, max_tokens=200)
    logger.info(f"Relevance check: {raw}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        result = json.loads(raw)
        if "is_relevant" not in result:
            return {"is_relevant": True, "needs_sql": True}
        if result.get("is_relevant") and "needs_sql" not in result:
            result["needs_sql"] = True
        return result
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse relevance check JSON: {raw}")
        return {"is_relevant": True}


# ---------------------------------------------------------------------------
# 1. GEOGRAPHIC ENTITY EXTRACTION
# ---------------------------------------------------------------------------
async def extract_geo_entity(user_message: str, history: list[dict]) -> dict:
    """
    Ask Claude to identify ALL geographic names in the user's message and
    produce a BigQuery LIKE-based lookup SQL for each one.

    Returns a dict:
      {
        "has_geo": True,
        "entities": [
          {
            "geo_type": "district" | "block" | "facility" | "state",
            "user_input_name": "<name the user typed>",
            "lookup_sql": "<SELECT DISTINCT ... LIKE ...>"
          },
          ...  (one entry per geographic entity mentioned)
        ]
      }
    OR:
      {
        "has_geo": False
      }
    """
    extraction_prompt = f"""Analyse the following message and identify ALL geographic entities
(states, districts, blocks, or facilities) mentioned — there may be more than one.

Message: "{user_message}"

LANGUAGE RULE: The message may be in Hindi or another language. Always translate
each geographic name to its standard English spelling before using it in the lookup SQL.
For example: "अर्वाल" → "Arwal", "पटना" → "Patna", "गया" → "Gaya", "दरभंगा" → "Darbhanga".
The user_input_name field should also use the English transliteration.

Rules:
- Extract EVERY geographic entity mentioned. For comparison queries like "Patna vs Darbhanga"
  or "compare Muzaffarpur and Gaya", extract BOTH (or all) entities as separate array items.
- Use LOWER(...) LIKE LOWER('%name%') — never exact equality.
- For district: SELECT DISTINCT district_name, district_code FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(district_name) LIKE LOWER('%name%') LIMIT 20
- For block:    SELECT DISTINCT sub_district_ulb_name, sub_district_ulb_code, district_name FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(sub_district_ulb_name) LIKE LOWER('%name%') LIMIT 20
- For facility: SELECT DISTINCT facility_name, facility_code, block_name, district_name FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(facility_name) LIKE LOWER('%name%') LIMIT 20
- For state:    SELECT DISTINCT state, state_code FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(state) LIKE LOWER('%name%') LIMIT 5
- If no geographic name is mentioned (follow-up question uses prior context), return has_geo = false.

Return ALL entities as an array — even if only one entity is mentioned.

Respond with ONLY valid JSON — no explanation, no markdown fences:
{{"has_geo": true, "entities": [
  {{"geo_type": "district", "user_input_name": "Patna", "lookup_sql": "SELECT DISTINCT district_name, district_code FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(district_name) LIKE LOWER('%patna%') LIMIT 20"}},
  {{"geo_type": "district", "user_input_name": "Darbhanga", "lookup_sql": "SELECT DISTINCT district_name, district_code FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets` WHERE LOWER(district_name) LIKE LOWER('%darbhanga%') LIMIT 20"}}
]}}
or
{{"has_geo": false}}
"""
    messages = list(history) + [{"role": "user", "content": extraction_prompt}]

    raw = await _call_fast(messages, max_tokens=1024)
    logger.info(f"Geo extraction raw response: {raw}")

    # Strip markdown code fences if Claude added them
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    try:
        result = json.loads(raw)
        # Normalize: if old single-entity format was returned, wrap it in the new array format
        if result.get("has_geo") and "geo_type" in result and "entities" not in result:
            result = {
                "has_geo": True,
                "entities": [{
                    "geo_type": result["geo_type"],
                    "user_input_name": result["user_input_name"],
                    "lookup_sql": result["lookup_sql"],
                }]
            }
        return result
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse geo extraction JSON: {raw}")
        return {"has_geo": False}


# ---------------------------------------------------------------------------
# 2. SQL GENERATION
# ---------------------------------------------------------------------------
async def generate_sql(
    user_question: str,
    confirmed_entities: list[dict],
    history: list[dict],
) -> str:
    """
    Generate the analytics SQL query for a user's question.

    Args:
        user_question:      The original question.
        confirmed_entities: List of dicts from BigQuery lookup rows, e.g.
                            [{"district_code": 191, "district_name": "Begusarai"},
                             {"district_code": 195, "district_name": "Darbhanga"}]
                            Empty list if geo comes from conversation history.
        history:            Previous conversation messages.

    Returns:
        A valid BigQuery SELECT query string, OR a string starting with
        "CANNOT_GENERATE: <reason>" if the question cannot be answered.
    """
    entity_context = ""
    if confirmed_entities:
        entity_context = (
            f"\nConfirmed geographic entities from database lookup:\n"
            f"{json.dumps(confirmed_entities, indent=2)}\n"
            f"CRITICAL: Use ONLY the numeric codes from the entities above in the WHERE clause — never filter by name string.\n"
            f"CRITICAL: NEVER guess, invent, or assume codes for any location NOT listed above.\n"
            f"If the question mentions a location that is not in this confirmed list, do NOT make up a code for it.\n"
        )
    else:
        entity_context = (
            "\nNo new geographic entity was specified — use the geographic context "
            "from the conversation history above.\n"
        )

    today_str = date.today().strftime("%Y-%m-%d")

    # Inject live COALESCE pairs from schema cache (fails silently — falls back
    # to the hardcoded Rule 4 list in the system prompt).
    live_schema_section = ""
    try:
        import schema_cache
        await schema_cache.ensure_loaded()
        rule4_text = schema_cache.get_rule4_text()
        if rule4_text:
            live_schema_section = f"\n{rule4_text}\n"
    except Exception as _sc_err:
        logger.warning(f"schema_cache unavailable — using hardcoded Rule 4: {_sc_err}")

    sql_prompt = f"""Generate the BigQuery SQL query to answer the following question.
Apply the relevant rules from the system prompt. Key reminders:
  • COALESCE pairs — use the LIVE COALESCE PAIRS list below (auto-detected from
    the current schema). Only fall back to Rule 4 in the system prompt for
    indicators whose column names differ between format versions (e.g. FIC, ANC).
  • Standard filters (Rule 1) — always apply.
  • Aggregation formula (Rule 2) — choose the RIGHT formula for the query type:
      - Coverage % against target  → Rule 2A (coverage formula with MAX(target))
      - Absolute count / total     → Rule 2B (plain SUM, no target)
      - Rate between two indicators → Rule 2C (SUM/NULLIF(SUM)*100)
      - Average                    → Rule 2D
  • Geographic codes (Rule 5) — use numeric codes, never name strings.
{live_schema_section}
Today's date: {today_str}
Use this to compute relative date ranges (e.g. "last 6 months") as plain string
literals in the WHERE clause — do NOT use TIMESTAMP_SUB (see Rule 10).

LANGUAGE RULE: If the question is in Hindi or any other non-English language,
translate it to English first (internally), then generate the SQL based on that
English interpretation.

Question: "{user_question}"
{entity_context}
IMPORTANT: Never refuse to generate SQL based on the time period requested.
You do not know what dates are in BigQuery. Generate the SQL for whatever
period is asked — if there is no data, BigQuery will return zero rows naturally.

USE CANNOT_GENERATE for these question types (do NOT attempt a SQL query):
1. Questions about the bot's own methodology, calculations, or what checks it runs:
   e.g. "What checks do you do for PREs?", "How do you calculate completeness?",
   "What is the PRE formula?", "How many correctness rules are there?"
   → These are answered from system knowledge, not from data.
2. Questions that are definitions or glossary lookups:
   e.g. "What does FIC mean?", "Explain BCG", "What is an AEFI?"
3. Questions that are completely unrelated to HMIS data or require information
   not in the BigQuery table.

Only use CANNOT_GENERATE if the question is truly unanswerable as a SQL query.

SELECT column guidance:
  • For coverage queries (Rule 2A): always include the coverage %, the raw
    SUM numerator, and the MAX(target)*COUNT(DISTINCT Month) denominator so
    the user can see the numbers behind the percentage.
  • For count/total queries (Rule 2B): include the SUM and a GROUP BY dimension
    (district, block, month, etc.) as appropriate.
  • For rate/ratio queries (Rule 2C): include both the computed rate AND the two
    raw SUMs (numerator and denominator) so the result is interpretable.
  • Always give columns clean aliases (e.g. AS institutional_deliveries, AS csection_rate_pct).

Respond with ONLY the SQL query — no explanation, no markdown fences.
If you truly cannot generate a valid query, respond with exactly:
CANNOT_GENERATE: <one-sentence reason>
"""
    messages = list(history) + [{"role": "user", "content": sql_prompt}]

    raw = await _call_sql(messages, max_tokens=8192)
    logger.info(f"Generated SQL:\n{raw}")

    # Strip markdown fences if present
    raw = re.sub(r"```(?:sql)?", "", raw).strip().rstrip("`")
    return raw


# ---------------------------------------------------------------------------
# 2b. SQL ERROR RECOVERY
# ---------------------------------------------------------------------------
async def fix_sql(broken_sql: str, bq_error: str, history: list[dict]) -> str:
    """
    BigQuery rejected a generated SQL query (e.g. column not found).
    Send the broken SQL + the error back to the model and ask for a corrected query.

    Returns a fixed SQL string, or a CANNOT_GENERATE: ... string on failure.
    """
    fix_prompt = f"""The following BigQuery SQL query failed with an error.
Fix the SQL so it runs correctly and answer the original question.
Always work in English when reasoning about the fix — the original question may
have been in Hindi but the SQL must be based on its English meaning.

--- BROKEN SQL ---
{broken_sql}

--- BIGQUERY ERROR ---
{bq_error}

Common causes and fixes:
- "Unrecognized name: X" means column X does not exist in the table.
  If X was the fallback in a COALESCE(A, X), just use A directly (no COALESCE needed).
  If X was the primary in a COALESCE(X, B), just use B directly.
  BigQuery's error message often says "Did you mean Y?" — use Y if suggested.
- Never invent column names. Only use columns that BigQuery confirms exist.

Respond with ONLY the corrected SQL query — no explanation, no markdown fences.
If you cannot fix it, respond with exactly: CANNOT_GENERATE: <one-sentence reason>
"""
    messages = list(history) + [{"role": "user", "content": fix_prompt}]
    raw = await _call_sql(messages, max_tokens=8192)
    logger.info(f"Fixed SQL:\n{raw}")
    raw = re.sub(r"```(?:sql)?", "", raw).strip().rstrip("`")
    return raw


# ---------------------------------------------------------------------------
# 2b-2. QUERY AMBIGUITY CHECK
# ---------------------------------------------------------------------------
async def check_query_ambiguity(
    user_question: str,
    confirmed_entities: list[dict],
    history: list[dict],
) -> dict:
    """
    Before generating SQL, decide whether the question has enough context to
    run unambiguously — without making any assumption.

    Returns:
      {"is_ambiguous": False}
      OR
      {"is_ambiguous": True, "clarification_question": "..."}

    On any JSON parse failure, returns {"is_ambiguous": False} (fail-open,
    so a bad parse never blocks the user from getting an answer).
    """
    today_str = date.today().strftime("%Y-%m-%d")
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1

    entity_context = (
        f"Confirmed geographic entities (already resolved from DB lookup): {json.dumps(confirmed_entities)}"
        if confirmed_entities
        else "No new geographic entity was resolved — may rely on conversation history."
    )

    prompt = f"""You are an expert HMIS data analyst reviewing a user's query before it runs.

Today: {today_str}
Current financial year: FY {fy_start}-{str(fy_start + 1)[-2:]} (April {fy_start} – March {fy_start + 1})
{entity_context}

User's question: "{user_question}"

Your task: reason carefully about what the user wants, then decide — should you ask ONE
clarifying question, or proceed directly?

Think like a senior analyst: if you ran this query right now, would the result genuinely
answer what the user asked? Or would you be making an assumption that could easily be wrong?

Ask a clarifying question when proceeding without it would mean:
  • Running a query over the wrong scope (e.g. all months when the user almost certainly
    wants a specific period — no analyst would pull "completeness for a district" without
    knowing which month)
  • Picking one interpretation when another equally plausible one exists and the results
    would look fundamentally different
  • The user mentioned something ambiguous that maps to meaningfully different SQL

Do NOT ask when:
  • A reasonable default exists and the user likely expects it (e.g. "this FY" when no
    date is given for a trend; "all facility types" when no type is mentioned; "combined
    total" when no breakdown is requested)
  • The question is conversational or explanatory — no SQL needed
  • Conversation history already resolves the gap
  • Any "[User clarified: X]" annotation in the question answers it — treat those as final
  • The query naturally spans all time (trends, rankings, availability checks)
  • A shorthand date like "nov25", "Q3FY26", "last 3 months", "this year" is used —
    these are unambiguous, resolve them mentally and proceed

When you do ask:
  • Ask exactly ONE focused question — bundle multiple gaps into one if needed
  • Suggest 2–3 concrete options so the user can just pick one
  • Be brief and friendly — users are field health officers, not data analysts
  • Reply in the same language the user wrote in (Hindi if they wrote in Hindi)

Respond with ONLY valid JSON — no explanation, no markdown fences:
{{"is_ambiguous": false}}
OR
{{"is_ambiguous": true, "clarification_question": "Which March did you mean — March 2025 or March 2026?"}}
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    raw = await _call_sql(messages, max_tokens=512)
    logger.info(f"Ambiguity check response: {raw}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        result = json.loads(raw)
        if "is_ambiguous" not in result:
            return {"is_ambiguous": False}
        return result
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse ambiguity check JSON: {raw}")
        return {"is_ambiguous": False}


# ---------------------------------------------------------------------------
# 2b-3. UNDERSTANDING CONFIRMATION (always fires before SQL)
# ---------------------------------------------------------------------------
async def formulate_understanding(
    user_question: str,
    confirmed_entities: list[dict],
    history: list[dict],
) -> str:
    """
    State what the bot understood about the user's query in plain language and
    ask for confirmation before running any SQL. If anything critical is missing
    (most often a time period), ask about it in the same message.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1

    entity_context = (
        f"Geographic entities already resolved from database: {json.dumps(confirmed_entities)}"
        if confirmed_entities
        else "No specific geography resolved — will rely on conversation context."
    )

    prompt = f"""You are an HMIS analytics assistant. Read the full conversation above
(including any corrections and clarifications), then state your current understanding
of what the user wants and ask for confirmation.

Today: {today_str}
Current FY: FY {fy_start}-{str(fy_start + 1)[-2:]} (April {fy_start}–March {fy_start + 1})
{entity_context}

Latest message from user: "{user_question}"

Write a short message (2–5 sentences) that:
1. Reflects the FULL conversation — incorporate any corrections the user already made,
   don't restate the original misunderstanding
2. Clearly states: indicator, geography, time period, analysis type
3. If something critical is still missing (most often a time period), ask about it here
4. End with "Shall I proceed?" — or the clarifying question if one is needed

Rules:
- Be concise and conversational — users are field health officers, not analysts
- Do NOT generate SQL or query anything — only confirm understanding
- Geography is confirmed above — do not ask about it again
- If clearly a trend query, do not ask for a time period
- Reply in the same language the user wrote in (Hindi or English)
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    reply = await _call_sql(messages, max_tokens=512)
    logger.info(f"Understanding formulated ({len(reply)} chars)")
    return reply


async def synthesize_final_query(
    history: list[dict],
    confirmed_entities: list[dict],
) -> str:
    """
    After the user confirms, derive a clean unambiguous query description from
    the full conversation history. This is passed to generate_sql instead of
    the raw annotated question string, so the SQL generator always gets a
    precise, well-formed instruction regardless of how the conversation evolved.
    """
    entity_context = (
        f"Confirmed geographic entities (use these numeric codes in the WHERE clause): "
        f"{json.dumps(confirmed_entities)}"
        if confirmed_entities
        else "No specific geography confirmed — rely on conversation context."
    )

    prompt = f"""The user just confirmed what they want to query. Based on the full
conversation above, write ONE clean description of the query to run.

{entity_context}

Include:
- Which indicator(s) to query (be specific — e.g. "Pentavalent-1 doses", not "vaccination")
- Which geography (reference the confirmed entity codes above, not name strings)
- Which time period (be precise — e.g. "latest available month", "April 2025", "FY 2024-25")
- What type of result (total count, coverage %, rate, trend by month, comparison, etc.)

Write it as a clear instruction for a SQL generator. No conversational language.
Example: "Total Penta1 doses given in Muzaffarpur district (district_code: 208) for the latest available month."
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    result = await _call_fast(messages, max_tokens=200)
    logger.info(f"Synthesized final query: {result}")
    return result


async def check_if_confirmed(user_response: str, history: list[dict]) -> dict:
    """
    Given the user's reply to an understanding confirmation, decide if they
    confirmed (proceed as stated) or corrected/changed something.

    Returns {"confirmed": True} or {"confirmed": False}.
    Fails open (True) on any parse error so a bad parse never blocks the user.
    """
    prompt = f"""The bot shared its understanding of a user's analytics query and asked for confirmation.
The user replied: "{user_response}"

Did the user confirm they want to proceed as described?
Confirmations include: yes, haan, sahi hai, correct, ok, proceed, go ahead, sure, bilkul, etc.
Corrections include: adding new info, changing time period, saying no, asking to adjust anything.

Respond with ONLY valid JSON — no explanation, no markdown:
{{"confirmed": true}}
OR
{{"confirmed": false}}
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    raw = await _call_fast(messages, max_tokens=64)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"confirmed": True}


# ---------------------------------------------------------------------------
# 2b-4. SQL FAILURE DIAGNOSIS
# ---------------------------------------------------------------------------
async def diagnose_sql_failure(
    broken_sql: str,
    bq_error: str,
    original_question: str,
    history: list[dict],
) -> dict:
    """
    After auto-fix retries are exhausted, decide whether asking the user a
    specific clarifying question could help resolve the SQL failure.

    Returns:
      {"needs_user_clarification": True, "clarification_question": "..."}
      OR
      {"needs_user_clarification": False}

    On parse failure, returns {"needs_user_clarification": False} (fail-open).
    """
    prompt = f"""A BigQuery SQL query generated from a user's question has failed,
and automatic fixing did not resolve the error.

Original user question: "{original_question}"

Failed SQL:
{broken_sql}

BigQuery error:
{bq_error}

Decide: would asking the user a specific clarifying question help resolve this error?

Situations where user clarification WOULD help:
- The error mentions a column that is ambiguous — the user may have meant a different
  (but similarly named) indicator that does exist in the schema.
- The user asked for a metric under an informal name that doesn't directly map to a
  column; asking which specific indicator they meant might unlock a valid column.
- The geographic scope or time period in the SQL caused an issue traceable to
  ambiguity in the original question (e.g. user said "this year" but in context it
  could mean two different financial years).
- The user requested an impossible combination; asking which part they actually need
  would make the query answerable.

Situations where user clarification would NOT help (don't ask):
- Column does not exist in the schema at all — no variant the user could name would fix it.
- Pure SQL syntax error in the generated query — a schema/model problem, not user input.
- Table access or permission error.
- Internal BigQuery errors unrelated to the question content.

If clarification would help, write a SHORT, specific question (1–2 sentences) that tells
the user exactly what to clarify. Offer likely options where possible.
Detect the language the user wrote in from the original question and reply in the SAME language.

Respond with ONLY valid JSON — no explanation, no markdown fences:
{{"needs_user_clarification": true, "clarification_question": "Did you mean X or Y?"}}
OR
{{"needs_user_clarification": false}}
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    raw = await _call_fast(messages, max_tokens=256)
    logger.info(f"SQL failure diagnosis: {raw}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        result = json.loads(raw)
        if "needs_user_clarification" not in result:
            return {"needs_user_clarification": False}
        return result
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse SQL failure diagnosis JSON: {raw}")
        return {"needs_user_clarification": False}


# ---------------------------------------------------------------------------
# 2c. CHART SPECIFICATION
# ---------------------------------------------------------------------------
async def get_chart_spec(
    user_question: str,
    results: list[dict],
    history: list[dict],
) -> dict:
    """
    Decide whether the query results should be visualised, and if so, how.

    Returns a dict such as:
      {
        "should_visualize": true,
        "chart_type": "gauge" | "line" | "bar" | "horizontal_bar" | "grouped_bar",
        "title": "BCG Coverage — Patna, April 2024",
        "x_column": "month",          # column name for x-axis / categories (null for gauge)
        "y_columns": ["bcg_coverage_pct"],
        "y_labels":  ["BCG Coverage (%)"],
        "x_label": "Month",
        "y_label": "Coverage (%)"
      }
    OR:
      { "should_visualize": false }
    """
    if not results:
        return {"should_visualize": False}

    columns = list(results[0].keys())
    sample  = results[:3]

    spec_prompt = f"""You are deciding how to visualise an HMIS query result as a chart.

DEFAULT RULE: Always visualise. Return should_visualize=false ONLY for the narrow
exceptions listed at the bottom. When in doubt, pick a chart type and visualise.

User question: "{user_question}"
Row count: {len(results)}
Columns: {columns}
Sample rows (up to 3): {sample}

═══ STEP 1 — DETECT DATA SHAPE (mandatory before picking chart type) ═══

A. LONG FORMAT: rows follow the pattern (date × category × value), e.g.
   [Month, antigen, coverage_pct] — 5 antigens × 4 months = 20 rows, or
   [Month, district_name, count].  Same date appears multiple times.
   → chart_type = "line" (or "area"), x_column = date col,
     y_columns = [value col], series_column = category col.
     The chart code draws one line/area per unique series value.

B. MATRIX FORMAT: rows form a grid — two categorical dims + one value, e.g.
   [district, antigen, coverage_pct].
   → chart_type = "heatmap", x_column = column dimension,
     series_column = row dimension, y_columns = [value col].

C. BEFORE/AFTER: each row is an entity; exactly 2 numeric columns hold the
   two time points, e.g. [district, dec25_pct, mar26_pct].
   → chart_type = "dumbbell" (horizontal) or "slope" (vertical slopegraph).
     y_columns = [col_before, col_after], x_column = entity column.

D. ORDERED SEQUENCE (funnel): rows represent stages of a dropout pipeline,
   values are expected to decrease, e.g. BCG → Penta1 → Penta3 → FIC.
   → chart_type = "funnel". Keep original row order (do NOT sort).

E. COMPOSITION (part-to-whole, no time): a category + value breakdown.
   ≤6 segments, proportions matter → "donut" or "pie".
   ≥3 segments, comparison matters → "stacked_bar" or "horizontal_bar".

F. WIDE FORMAT (standard): one row per entity, each numeric column is a metric.
   → Use the type selection table below.

═══ STEP 2 — PICK THE BEST CHART TYPE ═══

Use the first rule whose condition matches the data:

┌──────────────────────┬──────────────────────────────────────────────────────┐
│ Chart type           │ Use when                                             │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ gauge                │ 1 row, 1 numeric %, single KPI snapshot             │
│ line                 │ Time-series, ≥2 time points, continuous trend       │
│ area                 │ Time-series, want to emphasise volume/magnitude     │
│ stacked_area         │ Time-series composition — how parts add to a total  │
│ bar / column         │ ≤8 categories, short labels, single metric          │
│ horizontal_bar       │ >8 categories OR long labels (facilities, blocks)   │
│ lollipop             │ Ranked list, values vary widely — cleaner than bar  │
│ grouped_bar          │ 2–4 metrics side-by-side across ≤10 categories      │
│ stacked_bar          │ Composition per category — parts summing to total   │
│ dumbbell             │ Compare exactly 2 values per entity (2 time pts)   │
│ slope                │ Before/after for ≤15 entities (emphasises change)  │
│ funnel               │ Ordered dropout pipeline (BCG→Penta1→Penta3→FIC)   │
│ heatmap              │ Matrix: 2 categorical dims × 1 numeric value        │
│ pie                  │ Part-to-whole, ≤6 segments, proportions primary    │
│ donut                │ Part-to-whole ≤6 segments + show total in centre   │
│ scatter              │ Correlation between 2 numeric columns               │
│ bubble               │ Correlation + a 3rd size-encoded numeric dimension  │
│ waterfall            │ Sequential +/− contributions to a running total     │
│ treemap              │ Proportional area for many categories (>10)        │
│ radar                │ Multi-indicator profile comparison (≥4 metrics)    │
│ box_plot             │ Distribution of a metric across grouped categories  │
│ dot_plot             │ Multiple metrics per entity in a proportional grid  │
└──────────────────────┴──────────────────────────────────────────────────────┘

HMIS-specific hints:
• Coverage by month → line or area
• Coverage for multiple antigens in one month → lollipop or horizontal_bar
• Dropout: BCG → Penta1 → Penta3 → FIC → funnel
• District × antigen coverage matrix → heatmap
• Two-period comparison per block/district → dumbbell or slope
• Composition (home vs institutional deliveries over time) → stacked_area
• Single KPI (e.g. state FIC coverage 72.4%) → gauge
• DATA COMPLETENESS BREAKDOWN (one period, one row with overall + 4 sections)
  → horizontal_bar. Set x_column = null. y_columns = the per-section
    pct_* columns (and pct_fully_complete first if present). y_labels MUST
    be the plain-English section names from the map below — never "Section A".
    Sort happens inside the chart; just pass them in.
• PRE BREAKDOWN BY GROUP (one period, one row with overall + 5 groups)
  → horizontal_bar. Same shape: x_column = null, y_columns = the per-group
    pct_* columns, y_labels = plain-English group names from the map.
• CORRECTNESS BREAKDOWN BY RULE (overall + 4 rules)
  → horizontal_bar. Same shape, with rule labels spelled out.
• Per-facility / per-block ranking (multiple rows, one metric)
  → horizontal_bar (long names) or lollipop (when values vary widely).

═══ ANTI-PATTERNS — never do these ═══

✗ Vertical bar (chart_type=bar) for data quality sections — long English
  labels overlap. Always horizontal_bar for completeness / PRE / correctness
  breakdowns.
✗ pie / donut for >6 segments — slices become unreadable.
✗ line for ≤2 time points — use bar / lollipop instead.
✗ bar for >12 time points — switch to line.
✗ gauge unless the result is exactly 1 row × 1 percentage column.
✗ Raw column names in y_label / y_labels (e.g. "section_b1_pct_fully") —
  always translate using the plain-English map below.
✗ Sorting funnel charts — funnels rely on the original ordered sequence.

═══ STEP 3 — COLUMN MAPPING RULES ═══

x_column      : category / entity / date column placed on the primary axis.
                For scatter/bubble: the X-axis numeric column.
                For 1-row × multi-metric breakdowns (data quality): null.
y_columns     : list of numeric columns to plot.
                Prefer coverage/% columns over raw counts when both exist.
                Exclude ID/code columns (district_code, facility_code, etc.).
y_labels      : human-readable labels for each y column. MUST be plain
                English — never raw column names, never "Section A", "B1",
                "Group A1", "Rule A". Use the map below.
series_column : (long format / heatmap) categorical column that groups rows
                into separate series / heatmap rows.
size_column   : (bubble only) numeric column that controls bubble size.
label_column  : (scatter/bubble only) string column for point labels.

Keep title ≤60 chars. Include location and time period from the question.

═══ STEP 4 — PLAIN-ENGLISH LABELS (CRITICAL) ═══

NEVER put raw column names or technical codes (Section A, B1, B2, C, Group
A1–A4, Rule A–D) in chart labels — title, x_label, y_label, y_labels, axis
ticks. Charts go to non-technical field officers. Translate column names
EXACTLY using the maps below; this matches what the answer text says.

DATA COMPLETENESS — column → display label
  pct_fully_complete                          → "All sections (50 fields)"
  section_a_pct_fully  / a_pct_fully_*        → "ANC, Deliveries & Live Births"
  section_b1_pct_fully / b1_pct_fully_*       → "Birth-to-12-month immunisation"
  section_b2_pct_fully / b2_pct_fully_*       → "Booster & older-child doses"
  section_c_pct_fully  / c_pct_fully_*        → "AEFI & Session reporting"

PRE (Probable Reporting Errors) — column → display label
  pct_pre / pct_pre_overall                    → "Overall PRE rate"
  pct_facilities_zero_pre                      → "Facilities with zero PREs"
  group_a1_pct / pct_a1 / a1_*                 → "6-week visit (Penta1 vs OPV1, IPV1, Rotavirus-1, PCV1)"
  group_a2_pct / pct_a2 / a2_*                 → "10-week visit (Penta2 vs OPV2, Rotavirus-2)"
  group_a3_pct / pct_a3 / a3_*                 → "14-week visit (Penta3 vs OPV3, IPV2, Rotavirus-3, PCV2)"
  group_a4_pct / pct_a4 / a4_*                 → "9–11 month visit (MR1 vs IPV3, PCV Booster, JE1)"
  group_b_pct  / pct_b  / b_*                  → "Logical impossibility checks"

CORRECTNESS — column → display label
  pct_correct_100                              → "Facilities passing all 4 rules"
  rule_a_pct_correct / pct_correct_rule_a      → "HepB0 vs Deliveries"
  rule_b_pct_correct / pct_correct_rule_b      → "Sessions Held vs Planned"
  rule_c_pct_correct / pct_correct_rule_c      → "FIC vs MR1"
  rule_d_pct_correct / pct_correct_rule_d      → "AEFI Deaths vs Serious cases"

GENERAL CLEAN-UP — for any other column you map to a label:
  • Strip leading prefixes like "_9_2_5_a_", "m1_", "_4_1_3_b_"
  • Strip trailing _pct / _percent — write "(%)" inside x_label / y_label instead
  • Replace underscores with spaces and Title-Case the result
  • For COALESCE'd indicator columns, use the indicator's friendly name
    ("FIC Male", "BCG", "Pentavalent 3", "Institutional Deliveries", not the raw column)
  • For target / denominator columns, prefer descriptive names ("Estimated Infants")

ONLY return should_visualize=false when ALL of the following are true:
  • The result is a single row AND the only numeric columns are IDs or date counts with
    no meaningful magnitude (e.g. "how many months of data exist?" → MIN/MAX/COUNT only).
  • OR the result has zero numeric columns at all.

═══ STEP 5 — should_export_excel DECISION (CRITICAL) ═══

DEFAULT: true.  The user expects a downloadable Excel for nearly every
query result so they always have a copy of the underlying data. Override
to false ONLY in the narrow scalar cases listed below — when in doubt,
choose TRUE.

Set should_export_excel = TRUE whenever ANY of these is true:
  • The result has 2+ rows of any kind (lists, rankings, trends,
    comparisons, multi-entity breakdowns)
  • The result has 1 row but 2+ meaningful numeric columns (multi-metric
    summaries like data quality with overall + 4 sections, PRE rate with
    overall + 5 groups, correctness with overall + 4 rules)
  • Per-entity breakdowns (per facility, per block, per district, per
    month, per antigen, per section, per rule, per group)
  • Any time-series result (≥ 2 months of data)
  • Any data quality result — completeness, correctness, or PREs — always
  • Multi-month aggregates that include raw counts/numbers in the output
  • The user explicitly asked for a list, ranking, comparison, table, or
    "show me the breakdown / data / numbers"

Set should_export_excel = FALSE only when ALL of these hold:
  • The result has exactly 1 row, AND
  • That row contains at most 1 meaningful numeric column (one coverage %,
    one count, one rate), AND
  • The text answer fully and unambiguously communicates that single number

Examples (apply the rule above; do NOT memorise these — they're illustrative):
  • "What is BCG coverage in Patna in March 2026?" → 1 row × 1 metric → FALSE
  • "How many BCG doses given in Patna in March 2026?" → 1 row × 1 count → FALSE
  • "What months does the data cover?" → scalar availability check → FALSE
  • "Data completeness in Patna for March 2026" → 1 row × 5 metrics → TRUE
  • "PRE rate in Darbhanga in March 2026 by group" → multi-metric → TRUE
  • "Top 10 districts by FIC coverage in March 2026" → 10 rows → TRUE
  • "BCG coverage trend in Patna FY 2024-25" → 12 monthly rows → TRUE
  • "Compare Penta 3 across blocks in Darbhanga" → multi-row → TRUE

When in doubt → TRUE. An extra Excel attachment is low-cost; missing
data is high-cost.

Respond with ONLY valid JSON — no explanation, no markdown fences.
Include only the fields that apply to the chosen chart type.
Optional fields: series_column, size_column, label_column, x_label, y_label.

Examples:
{{"should_visualize": true, "should_export_excel": true, "chart_type": "line", "title": "BCG Coverage — Patna FY25", "x_column": "Month", "y_columns": ["bcg_coverage_pct"], "y_labels": ["BCG Coverage (%)"], "x_label": "Month", "y_label": "Coverage (%)"}}
{{"should_visualize": true, "should_export_excel": true, "chart_type": "line", "title": "Low-Coverage Antigens — Darbhanga Dec 25–Mar 26", "x_column": "Month", "y_columns": ["coverage_pct"], "y_labels": ["Coverage (%)"], "series_column": "antigen", "x_label": "Month", "y_label": "Coverage (%)"}}
{{"should_visualize": true, "should_export_excel": true, "chart_type": "heatmap", "title": "Coverage Matrix — Patna Dec 2025", "x_column": "antigen", "y_columns": ["coverage_pct"], "y_labels": ["Coverage (%)"], "series_column": "district_name"}}
{{"should_visualize": true, "should_export_excel": true, "chart_type": "funnel", "title": "Vaccine Dropout — Muzaffarpur FY25", "x_column": "stage", "y_columns": ["doses"], "y_labels": ["Doses Administered"]}}
{{"should_visualize": true, "should_export_excel": false, "chart_type": "gauge", "title": "FIC Coverage — Bihar Mar 2026", "x_column": null, "y_columns": ["fic_coverage_pct"], "y_labels": ["FIC Coverage (%)"]}}
{{"should_visualize": false, "should_export_excel": false}}
"""
    messages = list(history) + [{"role": "user", "content": spec_prompt}]
    raw = await _call_sql(messages, max_tokens=400)
    logger.info(f"Chart spec raw response: {raw}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse chart spec JSON: {raw}")
        return {"should_visualize": False}

# ---------------------------------------------------------------------------
# 2e. CONVERSATIONAL / META QUESTIONS
# ---------------------------------------------------------------------------
async def answer_conversational(user_question: str, history: list[dict]) -> str:
    """
    Answer a question that doesn't need a SQL query — e.g. follow-up
    explanations like "which doses did you sum?", "what formula did you use?",
    or general HMIS domain questions.

    Uses the conversation history as context so it can refer back to what was
    just computed or discussed.
    """
    prompt = f"""The user sent a message that does not require running a new database query.
Answer it naturally using the conversation history and your knowledge of HMIS.

Question: "{user_question}"

Guidelines:
- Respond naturally and conversationally — no rigid section headers needed.
- Keep it concise (under 200 words for the main answer).
- If the user is asking about a previous result ("which doses did you sum?",
  "what formula?", "explain this"), refer to that earlier exchange in your reply.
- Reply in the same language the user wrote in (Hindi or English).
- Do NOT attempt to generate SQL or query the database.

After your answer, always end with a *Next queries you could try* section
(single-asterisk bold header, WhatsApp-compatible format) with 2–3 specific follow-up
questions the user could ask next, grounded in the conversation context.
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    reply = await _call_sql(messages, max_tokens=1024)
    logger.info(f"Conversational reply generated ({len(reply)} chars)")
    return reply


# ---------------------------------------------------------------------------
# 3. KEY FINDINGS AND INSIGHTS  (GPT-4o — detailed analytical section)
# ---------------------------------------------------------------------------
async def generate_key_findings(
    user_question: str,
    results: list[dict],
    history: list[dict],
) -> str:
    """
    Produce the Key Findings and Insights section using the full SQL model (GPT-4o).
    This section goes beyond restating numbers — it uncovers trends, anomalies,
    disparities, and decision-relevant signals in the data.
    """
    formatted = _format_results(results)
    row_count = len(results)

    prompt = f"""You are helping a health worker or block-level officer in Bihar understand
immunisation and maternal health data. They are NOT a data analyst — explain everything
in simple, everyday language as if you are talking to them directly.

The user asked: "{user_question}"
The data returned {row_count} row(s):
{formatted}

Write the *Key Findings and Insights* section. Use exactly that header in bold (single asterisks: *Key Findings and Insights*).

RULES FOR THIS SECTION:
• Share only things that are actually useful or surprising — things the person should
  notice or act on. Do NOT just repeat the numbers from the Answer section.
• Use plain language. Instead of "dropout between doses", say "fewer children got the
  second dose than the first". Instead of "geographic disparity", say "some blocks are
  doing much better than others". Avoid all technical or government jargon.
• If performance is low, say it clearly and simply: "This is below the 90% target —
  around 1 in 10 children are being missed." Give real-world meaning to the numbers.
• Be specific — name the place and give the number. Vague bullets like "some areas
  need improvement" are not helpful.
• 3–5 bullets only. Short and clear — one sentence each if possible.

DATA QUALITY NAMING RULES (apply whenever the data contains report_section = completeness / correctness / pre):
• NEVER say "Section A", "Section B1", "Section B2", "Section C", "Group A1", "Rule A", etc.
• Instead use the full plain-English names AND spell out the actual indicators being checked:
  Completeness sections:
    - "ANC, Deliveries & Live Births" — checks whether these 6 fields are reported: TD1, TD2, TD Booster, Institutional Deliveries, Live Male Births, Live Female Births
    - "Birth-to-12-month immunisation" — checks whether these 23 vaccine doses are reported: BCG, HepB0, OPV0, Pentavalent 1/2/3, OPV 1/2/3, IPV 1/2/3, Rotavirus 1/2/3, PCV 1/2, MR1 (at 9 months), PCV Booster, FIC Male, FIC Female, Vitamin A1, JE1
    - "Booster & older-child doses" — checks whether these 15 doses are reported: Delayed MR1, Delayed DPT 1/2/3, DPT Booster (2yr), OPV Booster (2yr), MR2 (at 16 months), DPT Booster 1, OPV Booster, DPT 2nd Booster (5yr), TD10, TD16, Delayed JE1, JE Booster (2yr), JE2 (16 months)
    - "AEFI & Session reporting" — checks whether these 6 fields are reported: AEFI Minor cases, AEFI Severe cases, AEFI Serious cases, AEFI Deaths, Sessions Planned, Sessions Held
  PRE (co-administration mismatch) groups:
    - "6-week visit co-admin checks" — Pentavalent-1 count must equal OPV1, IPV1, Rotavirus-1, and PCV1 (all given together at 6 weeks)
    - "10-week visit co-admin checks" — Pentavalent-2 count must equal OPV2 and Rotavirus-2 (all given together at 10 weeks)
    - "14-week visit co-admin checks" — Pentavalent-3 count must equal OPV3, IPV2, Rotavirus-3, and PCV2 (all given together at 14 weeks)
    - "9–11 month visit co-admin checks" — MR1 count must equal IPV3, PCV Booster, and JE1 (all given together at 9–11 months)
    - "Logical impossibility checks" — Pentavalent-3 count should not exceed Pentavalent-1 (more 3rd doses than 1st doses is impossible); MR2 count should not exceed MR1; zero AEFI minor cases reported
  Correctness rules:
    - "HepB0 vs Deliveries" — HepB0 birth doses should not exceed total deliveries at the facility
    - "Sessions Held vs Planned" — sessions held should not exceed sessions planned
    - "FIC vs MR1" — fully immunised children count should not exceed MR1 doses (FIC requires MR1)
    - "AEFI Deaths vs Serious cases" — AEFI deaths should not exceed total serious AEFI cases reported
• When naming a check group in your response, use the full name and add its key indicators in brackets.
  Example: "6-week visit co-admin checks (Penta1 vs OPV1, IPV1, Rotavirus-1, PCV1)"
• Always express percentages as plain fractions too: "64.5% — roughly 2 in 3 facilities"
  or "28% — about 3 in 10 facilities". This helps field officers understand without
  needing to interpret decimal percentages.

TABLE RULE: When comparing 3 or more places across 2 or more numbers, use a simple
Markdown table instead of a list of bullets. Keep it to 2–3 columns max.

Use Indian number formatting (1,00,000). Round percentages to 1 decimal place.
Detect the language the user wrote in and reply in the SAME language (Hindi or English).
"""
    messages = list(history) + [{"role": "user", "content": prompt}]
    findings = await _call_sql(messages, max_tokens=800)
    logger.info(f"Key findings generated ({len(findings)} chars)")
    return findings


# ---------------------------------------------------------------------------
# 4. RESULT SUMMARIZATION  (Answer + What the agent did + Next queries)
# ---------------------------------------------------------------------------
async def summarize_results(
    user_question: str,
    results: list[dict],
    history: list[dict],
    generated_sql: str | None = None,
) -> str:
    """
    Produce three sections using GPT-4o-mini:
      • Answer — direct result with numerator / denominator / coverage %
      • What the agent did — plain-English description of the query performed
      • Next queries you could try — 2–3 contextual follow-up questions

    The caller stitches this with generate_key_findings() output to produce the
    full four-section response in the correct order.
    """
    formatted = _format_results(results)
    row_count = len(results)

    sql_context = ""
    if generated_sql:
        sql_context = (
            f"\nSQL query that was executed:\n{generated_sql}\n"
            f"Use this to describe what was queried in plain language for the "
            f"'What the agent did' section.\n"
        )

    summary_prompt = f"""You are helping a health worker or block-level officer in Bihar
understand data from the HMIS system. They are NOT a technical person. Write everything
in simple, clear language — like explaining to a colleague, not writing a report.

The user asked: "{user_question}"
The data returned {row_count} row(s):
{formatted}
{sql_context}
Write a response with EXACTLY these three sections. Use single-asterisk bold for each
header (WhatsApp Markdown format — single asterisks for bold), exactly as shown:

*Answer*
- Start with the direct answer in one plain sentence.
- Then give the key numbers: how many children / women / facilities, and the percentage
  if relevant. Show both the count and the total (e.g. "4,523 out of 5,100 children —
  that's 88.7% coverage").
- Use Indian number formatting (e.g., 1,00,000). Round percentages to 1 decimal place.
- If data is missing, say clearly: "No data was reported for this period."
- TABLE RULE: If the result has 3 or more places or time periods with 2 or more numbers
  each, show a simple table instead of a long list of sentences:
    | Place  | Coverage | Children vaccinated |
    |--------|----------|---------------------|
    | Patna  | 89.2%    | 4,523               |
  Only use a table when there are 3+ rows AND 2+ meaningful columns. Otherwise, keep it
  as plain sentences.
- Keep to 3–5 sentences (or one table + 1–2 sentences).

*What the agent did*
- In 1–2 simple sentences, explain what was looked up: which vaccine or indicator,
  which district or block, and which month or period.
- Write it like you are explaining to someone who has never used a database.
  Example: "I looked up how many children received the BCG vaccine in Gaya district
  during April 2024, using data reported by all health facilities there."
- Do NOT mention SQL, database queries, column names, or any technical terms.

*Next queries you could try*
- Suggest 2–3 follow-up questions that help the user dig deeper into the SAME topic
  they just asked about. Think like an analyst guiding someone through an investigation:
  what is the natural next question that would help them understand the problem better
  or identify where to take action?
- Follow a logical progression. Examples of good depth directions:
    • If they asked about a district → suggest breaking it down by block
    • If they asked about one vaccine → suggest comparing with a related vaccine or FIC
    • If they asked about one month → suggest looking at the trend over several months
    • If coverage is low → suggest looking at sessions held or data completeness
    • If they asked about completeness → suggest looking at which specific section is missing
    • If they asked about PREs → suggest drilling into which check is failing most
- Only suggest questions that can actually be answered using HMIS data in this system.
  Do NOT suggest questions about budgets, staff attendance, supply chain, or anything
  outside what HMIS reports.
- Write each suggestion as a ready-to-send question in plain language.
- Keep each suggestion to one short line.

DATA QUALITY NAMING RULES (apply whenever the data contains report_section = completeness / correctness / pre):
• Structure the Answer under three plain subheadings: *Data Completeness*, *Data Correctness*, *Reporting Errors (PREs)*
• NEVER say "Section A", "Section B1", "Section B2", "Section C", "Group A1", "Rule A", etc.
• Instead use full plain-English names AND spell out the actual indicators checked in brackets:
  Completeness sections:
    - "ANC, Deliveries & Live Births (TD1, TD2, TD Booster, Institutional Deliveries, Live Male Births, Live Female Births)"
    - "Birth-to-12-month immunisation (BCG, HepB0, OPV0, Pentavalent 1/2/3, OPV 1/2/3, IPV 1/2/3, Rotavirus 1/2/3, PCV 1/2, MR1, PCV Booster, FIC Male/Female, Vitamin A1, JE1)"
    - "Booster & older-child doses (Delayed MR1, Delayed DPT 1/2/3, DPT Booster 2yr, OPV Booster 2yr, MR2 at 16m, DPT Booster 1, OPV Booster, DPT 2nd Booster 5yr, TD10, TD16, Delayed JE1, JE Booster 2yr, JE2 at 16m)"
    - "AEFI & Session reporting (AEFI Minor, AEFI Severe, AEFI Serious, AEFI Deaths, Sessions Planned, Sessions Held)"
  PRE (co-administration mismatch) groups:
    - "6-week visit co-admin checks (Penta1 should equal OPV1, IPV1, Rotavirus-1, PCV1)"
    - "10-week visit co-admin checks (Penta2 should equal OPV2, Rotavirus-2)"
    - "14-week visit co-admin checks (Penta3 should equal OPV3, IPV2, Rotavirus-3, PCV2)"
    - "9–11 month visit co-admin checks (MR1 should equal IPV3, PCV Booster, JE1)"
    - "Logical impossibility checks (Penta3 count should not exceed Penta1; MR2 should not exceed MR1; zero AEFI minor reported)"
  Correctness rules:
    - "HepB0 vs Deliveries (HepB0 birth doses should not exceed total deliveries)"
    - "Sessions Held vs Planned (sessions held should not exceed sessions planned)"
    - "FIC vs MR1 (fully immunised children count should not exceed MR1 doses)"
    - "AEFI Deaths vs Serious cases (AEFI deaths should not exceed total serious AEFI)"
• Always express percentages as plain fractions too: "64.5% — roughly 2 in 3 facilities"
  or "28% — about 3 in 10 facilities".

Keep the total reply under 300 words.
Detect the language the user wrote in and reply in the SAME language (Hindi or English).
Output the section headers using single asterisks (*Header*) exactly as shown — do not use double asterisks.
"""
    messages = list(history) + [{"role": "user", "content": summary_prompt}]

    answer_and_meta = await _call_sql(messages, max_tokens=2048)
    logger.info(f"Answer/meta generated ({len(answer_and_meta)} chars)")
    return answer_and_meta


# ---------------------------------------------------------------------------
# Voice transcription (Whisper)
# ---------------------------------------------------------------------------

async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """
    Transcribe a voice message using OpenAI Whisper.
    Handles English, Hindi, and Hinglish (mixed).
    Returns the transcribed text, or empty string on failure.
    """
    try:
        response = await asyncio.wait_for(
            _client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, audio_bytes, "audio/ogg"),
                prompt=(
                    "Health data query. May contain Hindi, English, or Hinglish (mixed). "
                    "Topics: HMIS, immunization, BCG, FIC, ANC, Bihar, districts, blocks, coverage."
                ),
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Whisper transcription timed out after 120s")
    text = (response.text or "").strip()
    logger.info(f"Whisper transcription ({len(audio_bytes)//1024} KB): {text!r}")
    return text
